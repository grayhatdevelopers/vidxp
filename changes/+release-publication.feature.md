Align public release artifacts:

- stamp the Python package, Tauri package, desktop runtime manifest, and native app versions through semantic release
- build PyPI distributions and local/control/worker container images from the exact validated release commit
- build unsigned NSIS, DMG, and AppImage desktop installers from locked target-specific profiles and attach them to the matching GitHub release
- publish the GitHub release only after its package and desktop artifacts have passed their release gates
- preserve the rendered release notes through artifact handoff and final publication
- verify the minimal wheel separately from optional capability, UI, MCP, server, benchmark, and test extras
- keep the repository README's relative assets while rewriting them only in the temporary PyPI build
