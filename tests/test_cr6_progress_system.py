import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "contract_parser" / "static" / "index.html"


def test_cr6_dom_markup_elements():
    """Verify presence of top-edge progress bar and extraction milestone modal markup."""
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert 'id="global-top-progress"' in content, "Missing #global-top-progress element"
    assert 'id="extraction-modal-overlay"' in content, "Missing #extraction-modal-overlay container"
    assert 'id="extraction-timer"' in content, "Missing #extraction-timer element"
    assert 'id="extraction-pulse-bar"' in content, "Missing #extraction-pulse-bar element"
    for i in range(1, 5):
        assert f'id="milestone-card-{i}"' in content, f"Missing milestone-card-{i}"


def test_cr6_css_styling_and_keyframes():
    """Verify presence of hardware-accelerated CSS tokens, animations, and zero-layout shift rules."""
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert "#global-top-progress {" in content
    assert ".extraction-modal-overlay {" in content
    assert ".extraction-modal-card {" in content
    assert "backdrop-filter: blur(14px)" in content or "-webkit-backdrop-filter: blur(14px)" in content
    assert "@keyframes extractionShimmer" in content
    assert "@keyframes pulseIcon" in content
    assert ".global-error-toast {" in content
    assert ".pane-dimmed {" in content


def test_cr6_javascript_telemetry_interceptor():
    """Verify fetch interceptor, reference counting, and milestone state controllers."""
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert "let activeServerRequests = 0;" in content
    assert "function startGlobalProgress()" in content
    assert "function finishGlobalProgress()" in content
    assert "function abortGlobalProgress()" in content
    assert "function showExtractionModal(filename)" in content
    assert "function setMilestoneState(index, state)" in content
    assert "function handleExtractionSuccess()" in content
    assert "function hideExtractionModal()" in content
    assert "function handleServerError(statusCode, message, isUpload)" in content
    assert "function displayGlobalErrorToast(message)" in content
    assert "const originalFetch = window.fetch;" in content
    assert 'url.includes("/api/v1/")' in content
    assert 'url.includes("/api/v1/documents/upload")' in content
    assert "error.name === \"AbortError\"" in content


def test_cr6_zero_audio_mandate():
    """Verify strict adherence to zero audio enforcement across all assets."""
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert "AudioContext" not in content, "Forbidden AudioContext detected in index.html"
    assert "webkitAudioContext" not in content, "Forbidden webkitAudioContext detected in index.html"
    assert "<audio" not in content.lower(), "Forbidden <audio> tag detected in index.html"
    assert ".play()" not in content, "Forbidden media play() call detected in index.html"


def test_cr6_upload_button_parity():
    """Verify that modal restoration routines bind to the actual DOM upload button ID."""
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert 'document.getElementById("upload-btn")' in content


def test_invenergy_icon_and_favicon():
    """Verify favicon metadata, header icon placement, and FastAPI asset endpoints."""
    from fastapi.testclient import TestClient
    from contract_parser.app import create_app

    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert '<link rel="icon" type="image/x-icon" href="/favicon.ico"' in content
    assert '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png"' in content
    assert '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"' in content
    assert '<img src="/favicon-32x32.png" width="20" height="20" alt="Invenergy Icon"' in content

    app = create_app()
    client = TestClient(app)

    res_fav = client.get("/favicon.ico")
    assert res_fav.status_code == 200
    assert "image" in res_fav.headers.get("content-type", "")

    res_png = client.get("/favicon-32x32.png")
    assert res_png.status_code == 200
    assert res_png.headers.get("content-type") == "image/png"

    res_apple = client.get("/apple-touch-icon.png")
    assert res_apple.status_code == 200
    assert res_apple.headers.get("content-type") == "image/png"


def test_invtest123_read_only_dom_controls():
    """Verify invtest123 user authentication recognition and DOM upload disabling."""
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert 'invtest123' in content
    assert 'applyUserPermissions(user)' in content
    assert 'input.disabled = true' in content
    assert 'btn.disabled = true' in content
    assert '👤 invtest123 (Read-Only)' in content
    assert 'currentUser === "invtest123"' in content


def test_bq_agent_fab_window_toggle_lifecycle():
    """Verify BigQuery Conversational Agent FAB and window display toggle lifecycle."""
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert ".bq-agent-window.open {" in content
    assert "display: flex !important;" in content
    assert "function toggleBqAgentWindow()" in content
    assert 'const isOpen = win.classList.toggle("open");' in content
    assert 'win.style.display = isOpen ? "flex" : "none";' in content
    assert 'bqWin.classList.remove("open");' in content
    assert 'bqWin.style.display = "none";' in content


def test_bq_agent_disclaimer_element():
    """Verify presence, positioning, and styling of AI disclaimer in chat window."""
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert ".bq-agent-disclaimer {" in content
    assert "color: var(--neutrals-gray);" in content
    assert "background: var(--neutrals-white);" in content
    assert '<div class="bq-agent-disclaimer">' in content
    assert "AI can make mistakes. Please verify all terms and figures against original executed agreements." in content



