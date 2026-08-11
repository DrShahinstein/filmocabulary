import json

import httpx
from django.test import SimpleTestCase, override_settings

from vocabulary.source_acquisition import (
    SourceAcquisitionError,
    SourceConfigurationError,
    SourceNotFoundError,
    acquire_automatic_source,
)


def feature_result(
    *,
    title="Zodiac",
    original_title="Zodiac",
    year="2007",
    imdb_id=443706,
    feature_type="Movie",
):
    return {
        "id": "12345",
        "type": "feature",
        "attributes": {
            "feature_id": "12345",
            "feature_type": feature_type,
            "title": title,
            "original_title": original_title,
            "movie_name": f"{year} - {original_title}",
            "year": year,
            "imdb_id": imdb_id,
        },
    }


def feature_search_result(*results):
    return {"data": list(results or (feature_result(),))}


def subtitle_search_result(
    *,
    title="Zodiac",
    year=2007,
    imdb_id=443706,
    file_id=123,
    file_name="Zodiac.2007.1080p.BluRay",
):
    return {
        "data": [
            {
                "id": "98765",
                "type": "subtitle",
                "attributes": {
                    "language": "en",
                    "download_count": 100,
                    "feature_details": {
                        "feature_type": "Movie",
                        "title": title,
                        "movie_name": f"{year} - {title}",
                        "year": year,
                        "imdb_id": imdb_id,
                    },
                    "files": [
                        {
                            "file_id": file_id,
                            # OpenSubtitles frequently returns a release name here,
                            # not a filename with a subtitle extension.
                            "file_name": file_name,
                        }
                    ],
                },
            }
        ]
    }


def download_result(
    *,
    host="dl.opensubtitles.com",
    file_name="Zodiac.2007.1080p.BluRay",
):
    return {
        "link": f"https://{host}/download/token/subfile/{file_name}",
        "file_name": file_name,
        "requests": 1,
        "remaining": 4,
        "message": "Download count successful.",
        "reset_time": "12 hours",
        "reset_time_utc": "2026-08-12T00:00:00.000Z",
    }


@override_settings(
    VOCABULARY_AUTO_SOURCE_PROVIDER="opensubtitles",
    OPENSUBTITLES_API_KEY="test-api-key",
    OPENSUBTITLES_USER_AGENT="Filmocabulary tests v1.0",
)
class OpenSubtitlesSourceAcquisitionTests(SimpleTestCase):
    def test_resolves_feature_then_searches_by_imdb_id_and_parses_subtitle(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/api/v1/features":
                return httpx.Response(200, json=feature_search_result())
            if request.url.path == "/api/v1/subtitles":
                return httpx.Response(200, json=subtitle_search_result())
            if request.url.path == "/api/v1/download":
                self.assertEqual(json.loads(request.content), {"file_id": 123})
                return httpx.Response(200, json=download_result())
            return httpx.Response(
                200,
                content=(
                    b"1\n00:00:01,000 --> 00:00:02,000\n"
                    b"We must scrutinize every detail.\n"
                ),
                headers={"content-type": "application/x-subrip"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        acquired = acquire_automatic_source(
            title="Zodiac",
            release_year=2007,
            client=client,
        )

        self.assertIsNotNone(acquired)
        self.assertEqual(acquired.provider, "OpenSubtitles")
        self.assertEqual(acquired.source_id, "123")
        self.assertEqual(
            acquired.document.text,
            "We must scrutinize every detail.",
        )

        feature_request, subtitle_request, download_request, content_request = requests
        self.assertEqual(feature_request.url.path, "/api/v1/features")
        self.assertEqual(feature_request.url.params["query"], "zodiac")
        self.assertEqual(feature_request.url.params["type"], "movie")
        self.assertEqual(feature_request.url.params["year"], "2007")

        self.assertEqual(subtitle_request.url.path, "/api/v1/subtitles")
        self.assertEqual(subtitle_request.url.params["imdb_id"], "443706")
        self.assertEqual(subtitle_request.url.params["languages"], "en")
        self.assertEqual(subtitle_request.url.params["type"], "movie")
        self.assertEqual(subtitle_request.url.params["order_by"], "download_count")
        self.assertEqual(subtitle_request.url.params["order_direction"], "desc")
        self.assertNotIn("query", subtitle_request.url.params)

        for api_request in (feature_request, subtitle_request, download_request):
            self.assertEqual(api_request.headers["api-key"], "test-api-key")
            self.assertEqual(
                api_request.headers["user-agent"],
                "Filmocabulary tests v1.0",
            )
            self.assertEqual(api_request.headers["accept"], "application/json")
        self.assertNotIn("api-key", content_request.headers)

    def test_supplied_prefixed_imdb_id_is_normalised_and_skips_feature_lookup(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/api/v1/subtitles":
                return httpx.Response(200, json=subtitle_search_result())
            if request.url.path == "/api/v1/download":
                return httpx.Response(200, json=download_result())
            return httpx.Response(
                200,
                content=(
                    b"1\n00:00:01,000 --> 00:00:02,000\n"
                    b"We must scrutinize every detail.\n"
                ),
                headers={"content-type": "text/plain"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        acquired = acquire_automatic_source(
            title="Zodiac",
            release_year=2007,
            imdb_id="tt0443706",
            client=client,
        )

        self.assertIsNotNone(acquired)
        self.assertEqual(requests[0].url.path, "/api/v1/subtitles")
        self.assertEqual(requests[0].url.params["imdb_id"], "443706")
        self.assertNotIn("/api/v1/features", [request.url.path for request in requests])

    def test_feature_resolution_selects_exact_movie_and_year(self):
        requests = []
        features = feature_search_result(
            feature_result(title="Zodiac", year="2010", imdb_id=1374456),
            feature_result(
                title="The Zodiac",
                original_title="The Zodiac",
                year="2007",
                imdb_id=469919,
            ),
            feature_result(title="Zodiac", year="2007", imdb_id=443706),
        )

        def handler(request):
            requests.append(request)
            if request.url.path == "/api/v1/features":
                return httpx.Response(200, json=features)
            if request.url.path == "/api/v1/subtitles":
                return httpx.Response(200, json=subtitle_search_result())
            if request.url.path == "/api/v1/download":
                return httpx.Response(200, json=download_result())
            return httpx.Response(
                200,
                content=(
                    b"1\n00:00:01,000 --> 00:00:02,000\n"
                    b"We must scrutinize every detail.\n"
                ),
                headers={"content-type": "text/plain"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        acquire_automatic_source(
            title="Zodiac",
            release_year=2007,
            client=client,
        )

        self.assertEqual(requests[1].url.params["imdb_id"], "443706")

    def test_uses_file_id_when_search_file_name_has_no_srt_extension(self):
        def handler(request):
            if request.url.path == "/api/v1/subtitles":
                return httpx.Response(
                    200,
                    json=subtitle_search_result(file_name="Zodiac BluRay release"),
                )
            if request.url.path == "/api/v1/download":
                self.assertEqual(json.loads(request.content), {"file_id": 123})
                return httpx.Response(
                    200,
                    json=download_result(file_name="Zodiac BluRay release"),
                )
            return httpx.Response(
                200,
                content=(
                    b"1\n00:00:01,000 --> 00:00:02,000\n"
                    b"We must scrutinize every detail.\n"
                ),
                headers={"content-type": "application/octet-stream"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        acquired = acquire_automatic_source(
            title="Zodiac",
            release_year=2007,
            imdb_id="443706",
            client=client,
        )

        self.assertIsNotNone(acquired)
        self.assertEqual(acquired.source_id, "123")

    def test_rejects_subtitle_metadata_for_a_different_imdb_id(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=subtitle_search_result(imdb_id=1375666),
                )
            )
        )
        self.addCleanup(client.close)

        with self.assertRaises(SourceNotFoundError):
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                imdb_id="443706",
                client=client,
            )

    def test_follows_only_allowlisted_https_download_redirects(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/api/v1/subtitles":
                return httpx.Response(200, json=subtitle_search_result())
            if request.url.path == "/api/v1/download":
                return httpx.Response(
                    200,
                    json=download_result(host="www.opensubtitles.com"),
                )
            if request.url.host == "www.opensubtitles.com":
                return httpx.Response(
                    302,
                    headers={
                        "location": (
                            "https://dl.opensubtitles.com/download/token/"
                            "subfile/Zodiac.srt"
                        )
                    },
                )
            return httpx.Response(
                200,
                content=(
                    b"1\n00:00:01,000 --> 00:00:02,000\n"
                    b"We must scrutinize every detail.\n"
                ),
                headers={"content-type": "application/x-subrip"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        acquired = acquire_automatic_source(
            title="Zodiac",
            release_year=2007,
            imdb_id="443706",
            client=client,
        )

        self.assertIsNotNone(acquired)
        self.assertEqual(requests[-2].url.host, "www.opensubtitles.com")
        self.assertEqual(requests[-1].url.host, "dl.opensubtitles.com")
        self.assertNotIn("api-key", requests[-2].headers)
        self.assertNotIn("api-key", requests[-1].headers)

    def test_rejects_download_link_outside_the_host_allowlist(self):
        def handler(request):
            if request.url.path == "/api/v1/subtitles":
                return httpx.Response(200, json=subtitle_search_result())
            return httpx.Response(
                200,
                json={
                    "link": "https://example.test/subtitle.srt",
                    "file_name": "subtitle.srt",
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        with self.assertRaisesRegex(SourceAcquisitionError, "unsafe download"):
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                imdb_id="443706",
                client=client,
            )

    def test_does_not_forward_api_key_to_an_untrusted_api_redirect(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                302,
                headers={"location": "https://example.test/steal-api-key"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        with self.assertRaisesRegex(SourceAcquisitionError, "unsafe.*redirect"):
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "api.opensubtitles.com")

    def test_missing_exact_feature_raises_not_found_before_subtitle_search(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                200,
                json=feature_search_result(
                    feature_result(title="Zodiac", year="2010"),
                ),
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        with self.assertRaises(SourceNotFoundError):
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertEqual([request.url.path for request in requests], ["/api/v1/features"])

    def test_empty_subtitle_search_raises_not_found(self):
        def handler(request):
            return httpx.Response(200, json={"data": []})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        with self.assertRaises(SourceNotFoundError):
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                imdb_id="443706",
                client=client,
            )

    def test_rejects_malformed_feature_metadata(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"unexpected": []})
            )
        )
        self.addCleanup(client.close)

        with self.assertRaisesRegex(SourceAcquisitionError, "invalid response"):
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                client=client,
            )

    def test_rejects_malformed_subtitle_metadata(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"unexpected": []})
            )
        )
        self.addCleanup(client.close)

        with self.assertRaisesRegex(SourceAcquisitionError, "invalid response"):
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                imdb_id="443706",
                client=client,
            )

    def test_rejects_malformed_download_metadata(self):
        def handler(request):
            if request.url.path == "/api/v1/subtitles":
                return httpx.Response(200, json=subtitle_search_result())
            return httpx.Response(200, json={"remaining": 4})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)

        with self.assertRaisesRegex(SourceAcquisitionError, "invalid download response"):
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                imdb_id="443706",
                client=client,
            )

    def test_unauthorized_api_response_is_reported_as_configuration_error(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    401,
                    json={"message": "API key not found"},
                )
            )
        )
        self.addCleanup(client.close)

        with self.assertRaises(SourceConfigurationError):
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                client=client,
            )

    def test_rate_limited_api_response_is_safe_and_retryable(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    429,
                    json={"message": "Too many requests"},
                    headers={"retry-after": "60"},
                )
            )
        )
        self.addCleanup(client.close)

        with self.assertRaises(SourceAcquisitionError) as raised:
            acquire_automatic_source(
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertNotIn("test-api-key", str(raised.exception))


class AutomaticSourceConfigurationTests(SimpleTestCase):
    @override_settings(VOCABULARY_AUTO_SOURCE_PROVIDER="")
    def test_blank_provider_disables_automatic_lookup(self):
        self.assertIsNone(
            acquire_automatic_source(title="Zodiac", release_year=2007)
        )

    @override_settings(
        VOCABULARY_AUTO_SOURCE_PROVIDER="opensubtitles",
        OPENSUBTITLES_API_KEY="",
    )
    def test_selected_provider_requires_an_api_key(self):
        with self.assertRaisesRegex(SourceConfigurationError, "API key"):
            acquire_automatic_source(title="Zodiac", release_year=2007)
