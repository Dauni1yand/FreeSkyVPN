"""Отпечаток кода, по которому видно устаревший образ.

Dockerfile копирует `head/` внутрь образа, поэтому `docker compose up -d`
без `--build` поднимает прежний код. Снаружи это неотличимо от успешного
обновления: git pull прошёл, контейнер перезапустился, ошибка осталась
прежней — с тем же номером строки, что и до исправления.

Отпечаток считает одна функция, которую вызывают с двух сторон: голова
изнутри контейнера и меню по рабочему каталогу. Поэтому важно не только
что она что-то считает, а что она замечает изменения и не замечает того,
что кодом не является.
"""

from __future__ import annotations

from app.version import source_fingerprint


def _tree(root, files: dict[str, str]):
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def test_the_same_tree_gives_the_same_answer(tmp_path):
    """Иначе меню ругалось бы на устаревший образ при каждом запуске."""
    first = _tree(tmp_path / "a", {"cli.py": "x = 1\n", "svc/a.py": "y = 2\n"})
    second = _tree(tmp_path / "b", {"cli.py": "x = 1\n", "svc/a.py": "y = 2\n"})
    assert source_fingerprint(first) == source_fingerprint(second)


def test_changed_code_changes_the_answer(tmp_path):
    """Собственно то, ради чего всё это: правка должна быть заметна."""
    root = _tree(tmp_path / "a", {"cli.py": "x = 1\n"})
    before = source_fingerprint(root)
    (root / "cli.py").write_text("x = 2\n", encoding="utf-8")
    assert source_fingerprint(root) != before


def test_a_renamed_file_changes_the_answer(tmp_path):
    """Путь входит в хэш: переименование — тоже изменение кода, и по одному
    содержимому оно бы потерялось."""
    root = _tree(tmp_path / "a", {"cli.py": "x = 1\n"})
    before = source_fingerprint(root)
    (root / "cli.py").rename(root / "main.py")
    assert source_fingerprint(root) != before


def test_a_new_file_changes_the_answer(tmp_path):
    root = _tree(tmp_path / "a", {"cli.py": "x = 1\n"})
    before = source_fingerprint(root)
    (root / "extra.py").write_text("z = 3\n", encoding="utf-8")
    assert source_fingerprint(root) != before


def test_compiled_files_are_ignored(tmp_path):
    """__pycache__ наполняется от одного лишь запуска и кодом не является.

    Без этого отпечаток внутри работающего контейнера расходился бы с
    рабочим каталогом просто потому, что там что-то импортировали.
    """
    root = _tree(tmp_path / "a", {"cli.py": "x = 1\n"})
    before = source_fingerprint(root)
    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "cli.cpython-311.py").write_text("мусор\n", encoding="utf-8")
    assert source_fingerprint(root) == before


def test_non_python_files_are_ignored(tmp_path):
    """Логи и данные рядом с кодом не должны выглядеть как новая версия."""
    root = _tree(tmp_path / "a", {"cli.py": "x = 1\n"})
    before = source_fingerprint(root)
    (root / "notes.txt").write_text("что-то\n", encoding="utf-8")
    assert source_fingerprint(root) == before


def test_the_real_package_produces_something_short_and_stable():
    """То, что печатает `app.cli version` и сравнивает меню."""
    from pathlib import Path

    import app

    value = source_fingerprint(Path(app.__file__).resolve().parent)
    assert len(value) == 12
    assert value.isalnum()
