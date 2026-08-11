(function (window, document) {
    "use strict";

    var announcer = document.getElementById("app-announcer");

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content && meta.content !== "NOTPROVIDED") {
            return meta.content;
        }

        var cookie = document.cookie
            .split(";")
            .map(function (part) { return part.trim(); })
            .find(function (part) { return part.indexOf("csrftoken=") === 0; });

        return cookie ? decodeURIComponent(cookie.split("=").slice(1).join("=")) : "";
    }

    function announce(message) {
        if (!announcer) {
            return;
        }

        announcer.textContent = "";
        window.setTimeout(function () {
            announcer.textContent = message;
        }, 30);
    }

    function renderIcons() {
        if (window.lucide) {
            window.lucide.createIcons({
                attrs: {
                    "aria-hidden": "true",
                    "stroke-width": 1.8
                }
            });
        }
    }

    function initDismissibleMessages(root) {
        root.querySelectorAll(".message__dismiss:not([data-ready])").forEach(function (button) {
            button.dataset.ready = "true";
            button.addEventListener("click", function () {
                var message = button.closest(".message");
                if (!message) {
                    return;
                }
                message.remove();
                announce("Notification dismissed.");
            });
        });
    }

    function initMovieFilter(root) {
        root.querySelectorAll("[data-movie-filter]:not([data-ready])").forEach(function (input) {
            input.dataset.ready = "true";
            input.addEventListener("input", function () {
                var query = input.value.trim().toLocaleLowerCase();
                var library = document.getElementById(input.getAttribute("aria-controls"));
                if (!library) {
                    return;
                }

                var cards = Array.from(library.querySelectorAll("[data-movie-card]"));
                var visibleCount = 0;
                cards.forEach(function (card) {
                    var matches = !query || (card.dataset.searchValue || "").indexOf(query) !== -1;
                    card.hidden = !matches;
                    if (matches) {
                        visibleCount += 1;
                    }
                });

                var empty = library.querySelector("[data-filter-empty]");
                if (empty) {
                    empty.hidden = visibleCount !== 0;
                }
            });
        });
    }

    function initSelectionForms(root) {
        root.querySelectorAll("[data-selection-form]:not([data-ready]), .quiz-form:not([data-ready])").forEach(function (form) {
            form.dataset.ready = "true";
            var checkboxes = Array.from(form.querySelectorAll('input[type="checkbox"][name="movies"]'));
            var counter = form.querySelector("[data-selection-count]");
            var submit = form.querySelector('button[type="submit"]');

            function updateSelection() {
                var selected = checkboxes.filter(function (checkbox) { return checkbox.checked; }).length;
                if (counter) {
                    counter.textContent = selected + " selected";
                }
                if (submit) {
                    submit.disabled = selected === 0;
                }
            }

            checkboxes.forEach(function (checkbox) {
                checkbox.addEventListener("change", updateSelection);
            });
            updateSelection();
        });
    }

    function initFocusButtons(root) {
        root.querySelectorAll("[data-focus-target]:not([data-ready])").forEach(function (button) {
            button.dataset.ready = "true";
            button.addEventListener("click", function () {
                var target = document.querySelector(button.dataset.focusTarget);
                if (!target) {
                    return;
                }
                target.scrollIntoView({ behavior: "smooth", block: "center" });
                window.setTimeout(function () { target.focus(); }, 220);
            });
        });
    }

    function updateDashboardQuizState() {
        var workspace = document.querySelector(".dashboard-workspace");
        var launchRegion = document.getElementById("quiz-launch-region");
        if (!workspace || !launchRegion) {
            return;
        }

        var hasSetup = Boolean(launchRegion.querySelector(".quiz-setup"));
        var hasActiveQuiz = Boolean(launchRegion.querySelector(".quiz-panel, .quiz-complete"));
        workspace.classList.toggle("is-quiz-active", hasActiveQuiz && !hasSetup);
    }

    function updateLibraryCount() {
        var library = document.getElementById("movie-library");
        var countRoot = document.querySelector(".library-count");
        if (!library || !countRoot) {
            return;
        }

        var count = library.querySelectorAll("[data-movie-card]").length;
        var value = countRoot.querySelector(".library-count__value");
        var label = countRoot.querySelector(".library-count__label");
        if (value) {
            value.textContent = String(count);
        }
        if (label) {
            label.textContent = "saved film" + (count === 1 ? "" : "s");
        }
        countRoot.setAttribute("aria-label", count + " saved film" + (count === 1 ? "" : "s"));

        var filter = document.querySelector("[data-movie-filter]");
        if (filter && filter.value) {
            filter.dispatchEvent(new Event("input", { bubbles: true }));
        }
    }

    function updateVocabularyCount() {
        document.querySelectorAll(".vocabulary-sheet").forEach(function (sheet) {
            var itemCount = sheet.querySelectorAll(".vocabulary-item").length;
            var counter = sheet.querySelector(".vocabulary-sheet__count");
            if (counter) {
                counter.textContent = itemCount + " entr" + (itemCount === 1 ? "y" : "ies");
            }
            var list = sheet.querySelector(".vocabulary-items");
            if (itemCount === 0 && list && !list.querySelector(".vocabulary-items__empty")) {
                list.insertAdjacentHTML(
                    "beforeend",
                    '<li class="vocabulary-items__empty"><i data-lucide="captions-off" aria-hidden="true"></i><p>No vocabulary has been generated for this film.</p></li>'
                );
            }
        });

        var totalItems = document.querySelectorAll(".vocabulary-item").length;
        document.querySelectorAll(".vocabulary-total").forEach(function (counter) {
            counter.textContent = totalItems + " entr" + (totalItems === 1 ? "y" : "ies");
        });
    }

    function initRegion(root) {
        initDismissibleMessages(root);
        initMovieFilter(root);
        initSelectionForms(root);
        initFocusButtons(root);
        updateDashboardQuizState();
        updateLibraryCount();
        updateVocabularyCount();
        renderIcons();
    }

    function setRequestTargetBusy(event, isBusy) {
        var target = event.detail && event.detail.target;
        if (target && target.setAttribute) {
            target.setAttribute("aria-busy", isBusy ? "true" : "false");
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        announcer = document.getElementById("app-announcer");
        initRegion(document);

        if (window.jQuery) {
            window.jQuery.ajaxSetup({
                beforeSend: function (xhr, settings) {
                    if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type)) {
                        xhr.setRequestHeader("X-CSRFToken", csrfToken());
                    }
                }
            });
        }
    });

    document.body.addEventListener("htmx:configRequest", function (event) {
        event.detail.headers["X-CSRFToken"] = csrfToken();
    });

    document.body.addEventListener("htmx:beforeRequest", function (event) {
        setRequestTargetBusy(event, true);
    });

    document.body.addEventListener("htmx:afterRequest", function (event) {
        setRequestTargetBusy(event, false);
        var source = event.detail.elt;
        if (event.detail.successful && source && source.matches("[data-remove-on-success]")) {
            var item = source.closest(".vocabulary-item");
            if (item) {
                item.remove();
                updateVocabularyCount();
                renderIcons();
                announce("Vocabulary entry deleted.");
            }
        }
    });

    document.body.addEventListener("htmx:beforeSwap", function (event) {
        var status = event.detail.xhr ? event.detail.xhr.status : 0;
        if (status === 400 || status === 422 || status === 429 || status === 503) {
            event.detail.shouldSwap = true;
            event.detail.isError = false;
        }
    });

    document.body.addEventListener("htmx:afterSwap", function (event) {
        initRegion(event.detail.target || document);
        var autofocusTarget = event.detail.target && event.detail.target.querySelector("[data-autofocus], [autofocus]");
        if (autofocusTarget) {
            autofocusTarget.focus();
        }
    });

    document.body.addEventListener("htmx:oobAfterSwap", function (event) {
        initRegion(event.detail.target || document);
    });

    document.body.addEventListener("vocabularyChanged", function () {
        updateLibraryCount();
    });

    document.body.addEventListener("movieDeleted", function () {
        announce("Film deleted from your library.");
    });

    document.body.addEventListener("htmx:responseError", function () {
        announce("The request could not be completed. Please try again.");
    });

    document.body.addEventListener("htmx:sendError", function () {
        announce("The network request failed. Check your connection and try again.");
    });
})(window, document);
