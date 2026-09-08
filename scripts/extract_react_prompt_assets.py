from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile


def extract_notebook_string(notebook: Path, variable_name: str) -> str:
    payload = json.loads(notebook.read_text(encoding="utf-8"))
    matches: list[str] = []
    for cell in payload.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == variable_name for target in targets):
                continue
            value = ast.literal_eval(node.value)
            if not isinstance(value, str):
                raise TypeError(f"{variable_name} is not a static string")
            matches.append(value)
    if len(matches) != 1:
        raise ValueError(f"expected one {variable_name} assignment, found {len(matches)}")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notebook", type=Path, required=True)
    parser.add_argument("--variable", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = extract_notebook_string(args.notebook, args.variable)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=args.output.parent) as handle:
        handle.write(value.encode("utf-8"))
        temporary = Path(handle.name)
    os.replace(temporary, args.output)
    print(
        json.dumps(
            {
                "source_sha256": hashlib.sha256(args.notebook.read_bytes()).hexdigest(),
                "extracted_text_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
