from pathlib import PurePosixPath


def validate_storage_key(value: str) -> str:
    key = PurePosixPath(value)
    if (
        key.is_absolute()
        or ".." in key.parts
        or "\\" in value
        or value != key.as_posix()
    ):
        raise ValueError("storage_key must be a normalized relative key")
    return value
