$path = "desktop/src-tauri/target/release/vidxp-desktop.exe"
$stream = [System.IO.File]::OpenRead($path)
try {
    $reader = [System.IO.BinaryReader]::new($stream)
    $stream.Position = 0x3c
    $peOffset = $reader.ReadInt32()
    $stream.Position = $peOffset + 4 + 20 + 68
    $subsystem = $reader.ReadUInt16()
    if ($subsystem -ne 2) {
        throw "Expected Windows GUI subsystem 2, found $subsystem."
    }
} finally {
    $stream.Dispose()
}
