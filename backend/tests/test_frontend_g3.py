from pathlib import Path


def test_workspace_build_contract_and_local_entrypoint() -> None:
    root = Path(__file__).resolve().parents[2] / "frontend"
    entry = (root / "src/main.js").read_text(encoding="utf-8")
    package = (root / "package.json").read_text(encoding="utf-8")
    router = (root / "src/router.js").read_text(encoding="utf-8")
    assert "createPinia" in entry and ".use(router)" in entry
    assert "oidc-client-ts" not in package
    assert "createWebHistory" in router
    for page in (
        "LibrariesPage",
        "LibraryPage",
        "DocumentPage",
        "ChatPage",
        "TasksPage",
        "SystemPage",
    ):
        assert (root / f"src/pages/{page}.vue").is_file()
    assert not (root / "src/auth.js").exists()
