import io
import zipfile
from typing import Any

import pytest

from skye.db import Database
from skye.models import Scope
from skye.skills import SkillError, SkillService, bundle_from_upload, skill_paths

SKILL_MD = """---
name: basic-math
description: Add or multiply numbers.
---

Use this skill when you need a quick sum or product.
"""


@pytest.fixture
async def database(tmp_path: Any) -> Any:
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


def make_zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            payload = content if isinstance(content, bytes) else content.encode("utf-8")
            archive.writestr(path, payload)
    return buffer.getvalue()


def service(database: Database) -> SkillService:
    return SkillService(database, 1024 * 1024)


def test_markdown_skill_is_wrapped_into_a_zip() -> None:
    bundle = bundle_from_upload("SKILL.md", SKILL_MD.encode(), max_bytes=1024 * 1024)

    assert bundle.name == "basic-math"
    assert bundle.files == (("basic-math/SKILL.md", SKILL_MD.encode()),)
    assert "basic-math/SKILL.md" in skill_paths(bundle.archive)


def test_zip_skill_keeps_every_bundled_file() -> None:
    archive = make_zip(
        {
            "basic-math/SKILL.md": SKILL_MD,
            "basic-math/calculate.py": "def add(a, b): return a + b\n",
            "basic-math/data/table.csv": "a,b\n1,2\n",
        }
    )

    bundle = bundle_from_upload("basic_math.zip", archive, max_bytes=1024 * 1024)

    assert bundle.name == "basic-math"
    assert bundle.file_count == 3
    paths = {path for path, _payload in bundle.files}
    assert paths == {
        "basic-math/SKILL.md",
        "basic-math/calculate.py",
        "basic-math/data/table.csv",
    }


def test_zip_without_a_folder_is_rooted_under_the_skill_name() -> None:
    archive = make_zip({"SKILL.md": SKILL_MD, "helper.py": "print(1)\n"})

    bundle = bundle_from_upload("skill.zip", archive, max_bytes=1024 * 1024)

    assert {path for path, _payload in bundle.files} == {
        "basic-math/SKILL.md",
        "basic-math/helper.py",
    }


def test_unsafe_zip_paths_are_rejected() -> None:
    archive = make_zip({"../SKILL.md": SKILL_MD})

    with pytest.raises(SkillError, match="unsafe path"):
        bundle_from_upload("skill.zip", archive, max_bytes=1024 * 1024)


def test_zip_must_contain_exactly_one_skill_md() -> None:
    archive = make_zip({"a/SKILL.md": SKILL_MD, "b/SKILL.md": SKILL_MD})

    with pytest.raises(SkillError, match="exactly one SKILL.md"):
        bundle_from_upload("skill.zip", archive, max_bytes=1024 * 1024)


async def test_upload_stores_every_file_locally_without_a_provider(database: Database) -> None:
    skills = service(database)
    private = Scope("user", 42)
    archive = make_zip(
        {
            "basic-math/SKILL.md": SKILL_MD,
            "basic-math/calculate.py": "def add(a, b): return a + b\n",
        }
    )

    saved = await skills.upload(private, 42, "basic_math.zip", archive)
    stored = await database.get_skill(private, saved.id)

    assert stored is not None
    assert stored.openai_skill_id.startswith("local_")
    assert stored.name == "basic-math"
    assert stored.description == "Add or multiply numbers."
    assert stored.file_count == 2
    assert set(skill_paths(stored.archive)) == {
        "basic-math/SKILL.md",
        "basic-math/calculate.py",
    }
    assert await skills.list(Scope("chat", -100)) == []
    mounted = await skills.mounted(private)
    assert [item.id for item in mounted] == [saved.id]
    assert set(skill_paths(mounted[0].archive)) == {
        "basic-math/SKILL.md",
        "basic-math/calculate.py",
    }


async def test_delete_removes_the_local_skill(database: Database) -> None:
    skills = service(database)
    private = Scope("user", 42)
    saved = await skills.upload(private, 42, "SKILL.md", SKILL_MD.encode())

    removed = await skills.delete(private, saved.id)

    assert removed.id == saved.id
    assert await database.get_skill(private, saved.id) is None


async def test_duplicate_skill_names_are_rejected(database: Database) -> None:
    skills = service(database)
    private = Scope("user", 42)
    await skills.upload(private, 42, "SKILL.md", SKILL_MD.encode())

    with pytest.raises(SkillError, match="already in this chat"):
        await skills.upload(private, 42, "SKILL.md", SKILL_MD.encode())

    assert len(await skills.list(private)) == 1
