Application adapters now share one control-plane implementation for capability,
media, artifact, and shallow index-status operations. Local CLI composition now
loads model and workflow services only when a command uses them and closes opened
workflow clients through the Typer command lifecycle.
