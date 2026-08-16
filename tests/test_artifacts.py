from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from skye.artifacts import (
    GeneratedFile,
    collect_container_files,
    inspect_container_files,
    package_files,
    without_sandbox_links,
)


def citation(
    container_id: str = "cntr_1",
    file_id: str = "cfile_1",
    filename: str = "notes.md",
) -> SimpleNamespace:
    return SimpleNamespace(
        type="container_file_citation",
        container_id=container_id,
        file_id=file_id,
        filename=filename,
    )


def result_with(*output: object) -> SimpleNamespace:
    return SimpleNamespace(raw_responses=[SimpleNamespace(output=list(output))], new_items=[])


def test_sandbox_links_are_stripped_to_labels() -> None:
    text = "Done: [download Архитектура.md](sandbox:/mnt/data/Архитектура.md)"

    assert without_sandbox_links(text) == "Done: download Архитектура.md"


def test_inspect_reads_citations_and_container_id() -> None:
    cited, container_ids = inspect_container_files(
        result_with(
            SimpleNamespace(
                type="shell_call",
                environment=SimpleNamespace(type="container_reference", container_id="cntr_1"),
            ),
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", annotations=[citation()])],
            ),
        )
    )

    assert container_ids == frozenset({"cntr_1"})
    assert cited[0].file_id == "cfile_1"
    assert cited[0].path == "/mnt/data/notes.md"


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


class FakeContent:
    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies
        self.calls: list[tuple[str, str]] = []

    async def retrieve(self, file_id: str, *, container_id: str) -> Any:
        self.calls.append((container_id, file_id))
        return SimpleNamespace(content=self.bodies[file_id])


class FakeFiles:
    def __init__(self, items: list[SimpleNamespace], bodies: dict[str, bytes]) -> None:
        self.items = items
        self.content = FakeContent(bodies)

    def list(self, container_id: str, **_kwargs: object) -> Any:
        matching = [item for item in self.items if item.container_id == container_id]

        class Pages:
            def __init__(self, values: list[SimpleNamespace]) -> None:
                self._values = values

            def __aiter__(self) -> Pages:
                self._index = 0
                return self

            async def __anext__(self) -> SimpleNamespace:
                if self._index >= len(self._values):
                    raise StopAsyncIteration
                item = self._values[self._index]
                self._index += 1
                return item

        return Pages(matching)


class FakeClient:
    def __init__(self, items: list[SimpleNamespace], bodies: dict[str, bytes]) -> None:
        self.containers = SimpleNamespace(files=FakeFiles(items, bodies))


async def test_collects_cited_and_listed_assistant_files() -> None:
    listed = [
        SimpleNamespace(
            id="cfile_old",
            container_id="cntr_1",
            path="/mnt/data/old.txt",
            source="assistant",
            bytes=3,
            created_at=10,
        ),
        SimpleNamespace(
            id="cfile_new",
            container_id="cntr_1",
            path="/mnt/data/new.txt",
            source="assistant",
            bytes=3,
            created_at=50,
        ),
        SimpleNamespace(
            id="cfile_user",
            container_id="cntr_1",
            path="/mnt/data/upload.pdf",
            source="user",
            bytes=3,
            created_at=50,
        ),
    ]
    client = FakeClient(listed, {"cfile_1": b"cited", "cfile_new": b"new", "cfile_old": b"old"})

    files = await collect_container_files(
        cast(Any, client),
        result_with(
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        annotations=[citation(file_id="cfile_1", filename="cited.md")],
                    )
                ],
            )
        ),
        max_bytes=1024,
        created_after=40,
    )

    assert {item.filename for item in files} == {"cited.md", "new.txt"}
    assert client.containers.files.content.calls == [("cntr_1", "cfile_1"), ("cntr_1", "cfile_new")]


async def test_skips_files_over_the_size_limit() -> None:
    listed = [
        SimpleNamespace(
            id="cfile_big",
            container_id="cntr_1",
            path="/mnt/data/huge.bin",
            source="assistant",
            bytes=5000,
            created_at=50,
        )
    ]
    client = FakeClient(listed, {"cfile_big": b"x" * 5000})

    files = await collect_container_files(
        cast(Any, client),
        result_with(
            SimpleNamespace(
                type="shell_call",
                environment={"type": "container_reference", "container_id": "cntr_1"},
            )
        ),
        max_bytes=100,
        created_after=0,
    )

    assert files == ()
    assert client.containers.files.content.calls == []
