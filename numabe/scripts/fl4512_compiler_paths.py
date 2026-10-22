"""FL-4512 sole offline transaction-path policy for fact/program compilers."""
from pathlib import Path


AUTHORING_STORE = Path("assets/glyphs/generated/fl4512_authoring")


def transaction_path(value: str | Path, option: str, root: Path,
                     error_type: type[ValueError] = ValueError, *, authoring: bool = False) -> Path:
    path = Path(value)
    durable = authoring and path.is_relative_to(AUTHORING_STORE)
    anchor = AUTHORING_STORE if durable else Path(".run")
    root_real = root.resolve()
    store = root_real / anchor
    resolved = (root / path).resolve(strict=False)
    reason = ("authoring_path_must_be_in_immutable_program_store" if durable
              else "preview_path_must_be_relative_run_path")
    if (path.is_absolute() or ".." in path.parts or not path.parts
            or any(char.isspace() for char in str(path))
            or (root / anchor).resolve(strict=False) != store
            or root_real not in store.parents or store not in resolved.parents):
        raise error_type(f"{option}:{reason}")
    if durable:
        if len(path.relative_to(anchor).parts) < 2:
            raise error_type(f"{option}:{reason}")
    elif path.parts[0] != ".run" or len(path.parts) == 1:
        raise error_type(f"{option}:preview_path_must_start_with_.run")
    return path
