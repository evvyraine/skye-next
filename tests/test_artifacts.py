from skye.artifacts import GeneratedFile, package_files, without_sandbox_links


def test_sandbox_links_are_stripped_to_labels() -> None:
    text = "Done: [download Архитектура.md](sandbox:/mnt/data/Архитектура.md)"

    assert without_sandbox_links(text) == "Done: download Архитектура.md"


def test_package_sends_a_single_file() -> None:
    files = package_files([("/mnt/data/Архитектура.md", b"# architecture")])

    assert files == (GeneratedFile("Архитектура.md", b"# architecture"),)


def test_package_zips_a_directory() -> None:
    files = package_files(
        [
            ("/mnt/data/report/one.txt", b"1"),
            ("/mnt/data/report/two.txt", b"2"),
        ]
    )

    assert len(files) == 1
    assert files[0].filename == "report.zip"
    assert files[0].data[:2] == b"PK"


def test_package_zips_many_root_files() -> None:
    files = package_files([(f"/mnt/data/file-{index}.txt", b"x") for index in range(12)])

    assert len(files) == 1
    assert files[0].filename == "files.zip"


def test_package_ignores_paths_outside_the_data_root() -> None:
    assert package_files([("/tmp/evil.txt", b"x")]) == ()
    assert package_files([]) == ()
