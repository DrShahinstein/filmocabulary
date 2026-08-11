import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .ingestion import SourceDocument, SourceIngestionError, parse_source_text


logger = logging.getLogger(__name__)

OPENSUBTITLES_API_BASE_URL = "https://api.opensubtitles.com/api/v1"
OPENSUBTITLES_API_HOST = "api.opensubtitles.com"
OPENSUBTITLES_DOWNLOAD_HOSTS = frozenset(
    {
        "dl.opensubtitles.com",
        "www.opensubtitles.com",
    }
)
_IMDB_ID_PATTERN = re.compile(r"(?:tt)?(?P<digits>\d{1,10})", re.IGNORECASE)


class SourceAcquisitionError(Exception):
    """An automatic-source failure whose message is safe to display."""


class SourceNotFoundError(SourceAcquisitionError):
    pass


class SourceConfigurationError(SourceAcquisitionError):
    pass


class _SubtitleFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: int
    file_name: str = "subtitle.srt"


class _YearMixin(BaseModel):
    @field_validator("year", mode="before", check_fields=False)
    @classmethod
    def normalise_blank_year(cls, value):
        return None if value == "" else value


class _FeatureAttributes(_YearMixin):
    model_config = ConfigDict(extra="ignore")

    feature_type: str | None = None
    title: str | None = None
    original_title: str | None = None
    movie_name: str | None = None
    year: int | None = None
    imdb_id: int | str | None = None


class _FeatureResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    attributes: _FeatureAttributes


class _FeatureSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_FeatureResult]


class _FeatureDetails(_YearMixin):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    movie_name: str | None = None
    year: int | None = None
    imdb_id: int | str | None = None
    feature_type: str | None = None


class _SubtitleAttributes(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str
    download_count: int = 0
    feature_details: _FeatureDetails
    files: list[_SubtitleFile] = Field(min_length=1)


class _SubtitleResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    attributes: _SubtitleAttributes


class _SearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_SubtitleResult]


class _DownloadResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    link: str
    file_name: str = "subtitle.srt"


@dataclass(frozen=True, slots=True)
class AcquiredSource:
    document: SourceDocument
    provider: str
    source_id: str
    imdb_id: str


def normalise_imdb_id(value: str | int) -> str:
    if isinstance(value, bool):
        raise SourceAcquisitionError(
            "Automatic source lookup returned an invalid IMDb identifier."
        )
    match = _IMDB_ID_PATTERN.fullmatch(str(value).strip())
    if match is None:
        raise SourceAcquisitionError(
            "Automatic source lookup returned an invalid IMDb identifier."
        )
    return str(int(match.group("digits")))


def _normalise_movie_title(value: str) -> str:
    title = " ".join(value.split()).strip()
    trailing_article = re.fullmatch(r"(.+),\s*(the|an|a)", title, re.IGNORECASE)
    if trailing_article:
        title = f"{trailing_article.group(2)} {trailing_article.group(1)}"
    title = title.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^\w]+", " ", title).split())


def _positive_timeout() -> float:
    value = getattr(settings, "OPENSUBTITLES_TIMEOUT_SECONDS", 20.0)
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise SourceConfigurationError(
            "Automatic source lookup is not configured correctly."
        ) from exc
    if timeout <= 0:
        raise SourceConfigurationError(
            "Automatic source lookup is not configured correctly."
        )
    return timeout


def _source_max_bytes() -> int:
    value = getattr(settings, "VOCABULARY_SOURCE_MAX_BYTES", 2 * 1024 * 1024)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SourceConfigurationError(
            "Automatic source lookup is not configured correctly."
        )
    return value


def _safe_api_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == OPENSUBTITLES_API_HOST
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
    )


class OpenSubtitlesSourceResolver:
    def __init__(
        self,
        *,
        api_key: str,
        user_agent: str,
        client: Any | None = None,
    ) -> None:
        if (
            not isinstance(api_key, str)
            or not api_key.strip()
            or not isinstance(user_agent, str)
            or not user_agent.strip()
        ):
            raise SourceConfigurationError(
                "Automatic source lookup needs an OpenSubtitles API key."
            )
        self.api_key = api_key.strip()
        self.user_agent = user_agent.strip()
        self.owns_client = client is None
        self.client = client or httpx.Client(timeout=_positive_timeout())

    @property
    def api_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Api-Key": self.api_key,
            "User-Agent": self.user_agent,
        }

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def _request_json(self, method: str, path: str, **kwargs) -> Any:
        url = OPENSUBTITLES_API_BASE_URL + path
        current_method = method
        current_kwargs = kwargs
        for _ in range(5):
            headers = self.api_headers
            if "json" in current_kwargs:
                headers = {**headers, "Content-Type": "application/json"}
            try:
                response = self.client.request(
                    current_method,
                    url,
                    headers=headers,
                    follow_redirects=False,
                    **current_kwargs,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "OpenSubtitles %s %s request failed",
                    current_method,
                    path,
                )
                raise SourceAcquisitionError(
                    "Automatic source lookup is temporarily unavailable."
                ) from exc

            if response.is_redirect:
                location = response.headers.get("location")
                next_url = urljoin(url, location) if location else ""
                if not _safe_api_url(next_url):
                    raise SourceAcquisitionError(
                        "Automatic source lookup returned an unsafe redirect."
                    )
                url = next_url
                if response.status_code == 303 or (
                    response.status_code in {301, 302} and current_method == "POST"
                ):
                    current_method = "GET"
                    current_kwargs = {}
                else:
                    # A redirect location carries its own query string.
                    current_kwargs = {
                        key: value
                        for key, value in current_kwargs.items()
                        if key not in {"params"}
                    }
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status_code = response.status_code
                reason = response.headers.get("x-reason", "")
                try:
                    response_body = response.json()
                except ValueError:
                    response_body = {}
                if isinstance(response_body, dict):
                    reason = str(response_body.get("message") or reason)
                logger.warning(
                    "OpenSubtitles %s %s returned HTTP %s (%s)",
                    current_method,
                    path,
                    status_code,
                    reason[:160],
                )
                credential_rejection = status_code == 401 or (
                    status_code == 403
                    and any(
                        marker in reason.casefold()
                        for marker in (
                            "api key",
                            "authentication",
                            "unauthorized",
                            "invalid key",
                        )
                    )
                )
                if credential_rejection:
                    raise SourceConfigurationError(
                        "Automatic source lookup credentials were rejected by "
                        "OpenSubtitles."
                    ) from exc
                elif status_code == 429 or (
                    status_code == 403
                    and any(
                        marker in reason.casefold()
                        for marker in ("quota", "download limit", "too many")
                    )
                ):
                    message = (
                        "The OpenSubtitles request limit has been reached."
                    )
                elif status_code in {400, 406, 422}:
                    message = (
                        "OpenSubtitles rejected the automatic source request."
                    )
                else:
                    message = "Automatic source lookup is temporarily unavailable."
                raise SourceAcquisitionError(message) from exc

            if len(response.content) > 512 * 1024:
                raise SourceAcquisitionError(
                    "Automatic source lookup returned too much metadata."
                )
            try:
                return response.json()
            except ValueError as exc:
                raise SourceAcquisitionError(
                    "Automatic source lookup returned an invalid response."
                ) from exc

        raise SourceAcquisitionError(
            "Automatic source lookup returned too many redirects."
        )

    def _resolve_imdb_id(
        self,
        *,
        title: str,
        release_year: int | None,
    ) -> str:
        payload = self._request_json(
            "GET",
            "/features",
            params={
                "query": title.casefold(),
                "type": "movie",
                **({"year": release_year} if release_year is not None else {}),
            },
        )
        try:
            features = _FeatureSearchResponse.model_validate(payload)
        except ValidationError as exc:
            raise SourceAcquisitionError(
                "Automatic source lookup returned an invalid response "
                "while resolving the movie."
            ) from exc

        expected_title = _normalise_movie_title(title)
        matches: list[str] = []
        for result in features.data:
            details = result.attributes
            if details.feature_type and details.feature_type.casefold() != "movie":
                continue
            candidate_titles = (
                details.title,
                details.original_title,
                details.movie_name,
            )
            if not any(
                value and _normalise_movie_title(value) == expected_title
                for value in candidate_titles
            ):
                continue
            if release_year is not None and details.year != release_year:
                continue
            if details.imdb_id is None:
                continue
            try:
                candidate_id = normalise_imdb_id(details.imdb_id)
            except SourceAcquisitionError:
                continue
            if candidate_id not in matches:
                matches.append(candidate_id)

        if len(matches) != 1:
            raise SourceNotFoundError(
                "No unambiguous movie match was found automatically."
            )
        return matches[0]

    def _select_file(self, search_payload: Any, *, imdb_id: str) -> _SubtitleFile:
        try:
            search_response = _SearchResponse.model_validate(search_payload)
        except ValidationError as exc:
            raise SourceAcquisitionError(
                "Automatic source lookup returned an invalid response "
                "while searching subtitles."
            ) from exc

        matches: list[_SubtitleResult] = []
        for result in search_response.data:
            details = result.attributes.feature_details
            if result.attributes.language.casefold() != "en":
                continue
            if details.feature_type and details.feature_type.casefold() != "movie":
                continue
            if details.imdb_id is None:
                continue
            try:
                if normalise_imdb_id(details.imdb_id) != imdb_id:
                    continue
            except SourceAcquisitionError:
                continue
            matches.append(result)

        if not matches:
            raise SourceNotFoundError(
                "No matching English subtitles were found automatically."
            )
        matches.sort(
            key=lambda result: result.attributes.download_count,
            reverse=True,
        )
        # OpenSubtitles commonly omits the extension from file_name. file_id is
        # the authoritative download selector; downloaded bytes are parsed as SRT.
        return matches[0].attributes.files[0]

    def _download_subtitle(self, link: str) -> bytes:
        current_url = link
        for _ in range(5):
            parsed = urlparse(current_url)
            try:
                port = parsed.port
            except ValueError as exc:
                raise SourceAcquisitionError(
                    "Automatic source lookup returned an unsafe download link."
                ) from exc
            if (
                parsed.scheme != "https"
                or parsed.hostname not in OPENSUBTITLES_DOWNLOAD_HOSTS
                or port not in (None, 443)
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise SourceAcquisitionError(
                    "Automatic source lookup returned an unsafe download link."
                )
            try:
                response = self.client.get(
                    current_url,
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=False,
                )
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise SourceAcquisitionError(
                            "Automatic source download returned an invalid redirect."
                        )
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
            except SourceAcquisitionError:
                raise
            except httpx.HTTPError as exc:
                logger.warning(
                    "OpenSubtitles subtitle download failed",
                )
                raise SourceAcquisitionError(
                    "Automatic source download is temporarily unavailable."
                ) from exc

            content_type = response.headers.get("content-type", "")
            media_type = content_type.split(";", 1)[0].strip().casefold()
            if media_type and media_type not in {
                "application/octet-stream",
                "application/x-subrip",
                "text/plain",
            }:
                raise SourceAcquisitionError(
                    "Automatic source download did not return a subtitle file."
                )
            content = response.content
            if len(content) > _source_max_bytes():
                raise SourceAcquisitionError(
                    "The automatically downloaded subtitle file is too large."
                )
            return content

        raise SourceAcquisitionError(
            "Automatic source download returned too many redirects."
        )

    def acquire(
        self,
        *,
        title: str,
        release_year: int | None,
        imdb_id: str | int | None = None,
    ) -> AcquiredSource:
        resolved_imdb_id = (
            normalise_imdb_id(imdb_id)
            if imdb_id is not None
            else self._resolve_imdb_id(title=title, release_year=release_year)
        )
        search_payload = self._request_json(
            "GET",
            "/subtitles",
            params={
                "imdb_id": resolved_imdb_id,
                "languages": "en",
                "order_by": "download_count",
                "order_direction": "desc",
                "type": "movie",
            },
        )
        subtitle_file = self._select_file(
            search_payload,
            imdb_id=resolved_imdb_id,
        )
        download_payload = self._request_json(
            "POST",
            "/download",
            json={"file_id": subtitle_file.file_id},
        )
        try:
            download = _DownloadResponse.model_validate(download_payload)
            content = self._download_subtitle(download.link)
            document = parse_source_text(
                content,
                source_format="srt",
                filename=download.file_name or subtitle_file.file_name,
            )
        except ValidationError as exc:
            raise SourceAcquisitionError(
                "Automatic source lookup returned an invalid download response."
            ) from exc
        except SourceIngestionError as exc:
            raise SourceAcquisitionError(str(exc)) from exc

        return AcquiredSource(
            document=document,
            provider="OpenSubtitles",
            source_id=str(subtitle_file.file_id),
            imdb_id=resolved_imdb_id,
        )


def acquire_automatic_source(
    *,
    title: str,
    release_year: int | None,
    imdb_id: str | int | None = None,
    client: Any | None = None,
) -> AcquiredSource | None:
    provider = getattr(settings, "VOCABULARY_AUTO_SOURCE_PROVIDER", "")
    if not isinstance(provider, str):
        raise SourceConfigurationError(
            "Automatic source lookup is not configured correctly."
        )
    provider = provider.strip().casefold()
    if not provider:
        return None
    if provider != "opensubtitles":
        raise SourceConfigurationError(
            "Automatic source lookup is not configured correctly."
        )

    resolver = OpenSubtitlesSourceResolver(
        api_key=getattr(settings, "OPENSUBTITLES_API_KEY", ""),
        user_agent=getattr(
            settings,
            "OPENSUBTITLES_USER_AGENT",
            "Filmocabulary v1.0",
        ),
        client=client,
    )
    try:
        return resolver.acquire(
            title=title,
            release_year=release_year,
            imdb_id=imdb_id,
        )
    finally:
        resolver.close()
