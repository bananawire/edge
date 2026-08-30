{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = with pkgs; [ python313 uv sqlite curl ];

  shellHook = ''
    echo ""
    echo "Environment ready (Python 3.13 + uv)."
    echo "Sync deps:    uv sync"
    echo "Run app:      uv run python app.py"
    echo "Run tests:    uv run python -m unittest discover -s tests"
    echo ""
  '';
}
