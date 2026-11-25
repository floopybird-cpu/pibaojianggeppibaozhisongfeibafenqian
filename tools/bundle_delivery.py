"""Assemble three self-contained handoff archives from approved local materials."""
import argparse
import hashlib
from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
COURSE_FOLDERS = ("教材", "实验讲义和课件", "课程基础与复习", "补充讲义")
MATERIAL_TYPES = {".pdf", ".doc", ".docx", ".ppt", ".pptx"}


def file_hash(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def archive(destination, files, index=""):
    """Compare each uncompressed archive member to its source before publishing."""
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".zip", delete=False) as temp:
        scratch = Path(temp.name)
    try:
        expected = {name:file_hash(path) for name, path in files.items()}
        with ZipFile(scratch, "w", compression=ZIP_DEFLATED, compresslevel=3) as out:
            for name, path in files.items():
                out.write(path, name)
            if index:
                value = index.encode("utf-8-sig")
                out.writestr("资料目录.txt", value)
                expected["资料目录.txt"] = hashlib.sha256(value).hexdigest()
        with ZipFile(scratch) as packed:
            if sorted(packed.namelist()) != sorted(expected):
                raise ValueError(f"{destination.name} 文件清单不一致")
            for name, digest in expected.items():
                with packed.open(name) as member:
                    if hashlib.file_digest(member, "sha256").hexdigest() != digest:
                        raise ValueError(f"{destination.name} 文件校验失败 {name}")
        scratch.replace(destination)
        print(file_hash(destination), destination.name, len(expected), flush=True)
    finally:
        scratch.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="组装安装程序、课程资料、学生使用手册三个 ZIP，并逐项校验")
    parser.add_argument("--installer", required=True, type=Path, help="已编译的 Windows 安装程序 EXE")
    parser.add_argument("--materials", required=True, type=Path, help="已选好课程原件的目录，内有教材、实验讲义和课件等分类")
    parser.add_argument("--out", required=True, type=Path, help="交付文件夹")
    args = parser.parse_args()
    installer = args.installer.resolve(strict=True)
    materials = args.materials.resolve(strict=True)
    if installer.suffix.lower() != ".exe" or not installer.is_file():
        parser.error("请选择已生成的安装程序 EXE")
    files = {}
    for folder in COURSE_FOLDERS:
        for path in sorted((materials/folder).rglob("*")):
            if path.is_file() and path.suffix.lower() in MATERIAL_TYPES and not path.name.startswith("~$"):
                if not path.resolve().is_relative_to(materials):
                    parser.error(f"资料文件指向目录外部 {path.name}")
                files[path.relative_to(materials).as_posix()] = path
    for required in ("教材/", "实验讲义和课件/"):
        if not any(name.startswith(required) for name in files):
            parser.error("材料目录缺少 " + required)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Check all required inputs before replacing a previous package.
    student = (ROOT/"docs"/"物理实验助手操作说明.pdf").resolve(strict=True)
    maintainer = (ROOT/"docs"/"维护手册.pdf").resolve(strict=True)
    archive(out/"01-程序安装包.zip", {installer.name:installer, maintainer.name:maintainer})
    index = "课程资料目录\n\n课本可在「资料与知识」中整理为长期知识。实验讲义和课件按实验归类，新建实验时选择当次使用的文件。\n\n" + "\n".join(files) + "\n"
    archive(out/"02-配套讲义与教材.zip", files, index)
    archive(out/"03-使用手册.zip", {student.name:student})


if __name__ == "__main__":
    main()
