Align public release artifacts:

- stamp the Python package, Tauri package, desktop runtime manifest, and native app versions through semantic release
- build PyPI distributions and local/control/worker container images from the exact validated release commit
- verify the minimal wheel separately from optional capability, UI, MCP, server, benchmark, and test extras
- keep the repository README's relative assets while rewriting them only in the temporary PyPI build
