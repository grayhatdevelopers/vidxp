Store the default local repository and prepared models beneath the operating
system's per-user VidXP data directory instead of the shell's current directory
across CLI, browser UI, local MCP/server processes, and desktop operation.
Stop the owned local worker when those long-running local interfaces shut down
so closed UI/API/MCP processes do not leave repository workers behind.
