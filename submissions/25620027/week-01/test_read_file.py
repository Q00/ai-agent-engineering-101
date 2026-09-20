from pathlib import Path

from first_agent import read_file


DENIED = "denied: path outside the working directory"


def test_read_file_allows_file_in_working_directory(
    tmp_path: Path, monkeypatch,
) -> None:
    # Given
    note = tmp_path / "notes.txt"
    note.write_text("course note", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # When
    result = read_file("notes.txt")

    # Then
    assert result == "course note"


def test_read_file_denies_same_prefix_sibling(
    tmp_path: Path, monkeypatch,
) -> None:
    # Given
    working_directory = tmp_path / "work"
    sibling_directory = tmp_path / "work-private"
    working_directory.mkdir()
    sibling_directory.mkdir()
    outside_file = sibling_directory / "secret.txt"
    outside_file.write_text("outside", encoding="utf-8")
    monkeypatch.chdir(working_directory)

    # When
    result = read_file(str(outside_file))

    # Then
    assert result == DENIED


def test_read_file_denies_environment_file_inside_working_directory(
    tmp_path: Path, monkeypatch,
) -> None:
    # Given
    environment_file = tmp_path / ".env"
    environment_file.write_text("placeholder", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # When
    result = read_file(environment_file.name)

    # Then
    assert result == DENIED


def test_read_file_denies_unapproved_text_file_inside_working_directory(
    tmp_path: Path, monkeypatch,
) -> None:
    # Given
    private_note = tmp_path / "private.txt"
    private_note.write_text("private", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # When
    result = read_file(private_note.name)

    # Then
    assert result == DENIED


def test_read_file_denies_symlink_to_outside_file(
    tmp_path: Path, monkeypatch,
) -> None:
    # Given
    working_directory = tmp_path / "work"
    outside_directory = tmp_path / "private"
    working_directory.mkdir()
    outside_directory.mkdir()
    outside_file = outside_directory / "secret.txt"
    outside_file.write_text("outside", encoding="utf-8")
    link = working_directory / "linked-secret.txt"
    link.symlink_to(outside_file)
    monkeypatch.chdir(working_directory)

    # When
    result = read_file(link.name)

    # Then
    assert result == DENIED


def test_read_file_denies_regular_outside_path(
    tmp_path: Path, monkeypatch,
) -> None:
    # Given
    working_directory = tmp_path / "work"
    outside_file = tmp_path / "secret.txt"
    working_directory.mkdir()
    outside_file.write_text("outside", encoding="utf-8")
    monkeypatch.chdir(working_directory)

    # When
    result = read_file(str(outside_file))

    # Then
    assert result == DENIED
