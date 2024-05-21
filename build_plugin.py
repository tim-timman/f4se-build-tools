import argparse
import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Optional


F4SE_REPO = "https://github.com/ianpatt/f4se"
F4SE_COMMON_REPO = "https://github.com/ianpatt/common"


class Context(argparse.Namespace):
    dist_dir: Path
    build_dir: Path
    project_dir: Path
    platform_toolset: str
    f4se_revision: str
    include_extras: Optional[Path] = None

    # these are filled as we go
    f4se_dir: Path
    f4se_common_dir: Path
    src_project: Path
    build_project: Path
    build_solution: Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, required=True, help="Output directory of plugin artifacts.")
    parser.add_argument("--project-dir", type=Path, required=True, help="Directory containing vcxproj file.")
    parser.add_argument("--build-dir", type=Path, required=True, help="Location of the build folder.")
    parser.add_argument("--platform-toolset", required=True, type=str, help="Toolset to compile the plugin with.")
    parser.add_argument("--f4se-revision", required=True, type=str, help="Which commit of F4SE to use for compilation.")
    parser.add_argument("--include-extras", type=Path, help="Path to file structure of extra files to include.")

    ctx = parser.parse_args(namespace=Context())

    with contextlib.redirect_stdout(sys.stderr):
        fetch_f4se(ctx)
        patch_f4se(ctx)
        update_project_references(ctx)
        make_solution(ctx)
        build_plugin(ctx)
        package_plugin(ctx)

    return 0


def fetch_f4se(ctx: Context):
    """
    Fetch/checkout the F4SE (and common) repos to the specified
    revision into the build directory.
    """
    cmds: list[list[str]] = []

    if (f4se_dir := ctx.build_dir / "f4se").exists():
        cmds.append([
            "git", "-C", f4se_dir,
            "fetch", "--depth=1",
            "origin", f"{ctx.f4se_revision}:refs/remotes/origin/{ctx.f4se_revision}"
        ])
        cmds.append(["git", "-C", f4se_dir, "checkout", "--force"])
    else:
        cmds.append([
            "git", "clone",
            "--branch", ctx.f4se_revision,
            "--depth=1",
            F4SE_REPO,
            f4se_dir
       ])

    if (f4se_common_dir := ctx.build_dir / "common").exists():
        cmds.append([
            "git", "-C", f4se_common_dir,
            "fetch", "--depth=1",
            "origin", f"HEAD"
        ])
        cmds.append(["git", "-C", f4se_common_dir, "reset", "--hard", "FETCH_HEAD"])
    else:
        cmds.append([
            "git", "clone",
            "--depth=1",
            F4SE_COMMON_REPO,
            f4se_common_dir
       ])
    try:
        for cmd in cmds:
            print(f"Running: {shlex.join(map(str, cmd))}")
            subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise SystemExit(e.returncode)

    ctx.f4se_dir = f4se_dir
    ctx.f4se_common_dir = f4se_common_dir


def add_include_line(filepath: Path, include_line: str) -> bool:
    if not include_line.endswith("\n"):
        include_line = f"{include_line}\n"

    with open(filepath, "r+") as f:
        lines = f.readlines()
        if include_line in lines:
            return False
        f.seek(0)
        lines.insert(1, include_line)
        f.writelines(lines)
        f.truncate()
    return True


def patch_f4se(ctx: Context):
    """
    Makes changes to F4SE for plugin development under VS2015.

    # Makes the following changes in the build directory:
    ## f4se/f4se/f4se.vcxproj
     - Output a static library instead of a dynamic library for linking with plugins.

    ## f4se/f4se/BSSkin.h
     - Add missing header #include <xmmintrin.h> for use of __m128 type.

    ## f4se/f4se/PapyrusObjects.h
     - Add missing header #include <algorithm> for use of std::min.
    """
    f4se_project = ctx.f4se_dir / "f4se/f4se.vcxproj"
    project_text = f4se_project.read_text("utf-8")
    # Set Configuration Type to Static Library (original: Dynamic Library)
    sub_text = re.sub(
        r'(<PropertyGroup.*Label="Configuration">[\S\s]*?<ConfigurationType>)'
        r'(.*)'
        r'(</ConfigurationType>[\S\s]*?</PropertyGroup>)',
        repl=r"\1StaticLibrary\3",
        string=project_text,
        flags=re.MULTILINE,
    )
    f4se_project.write_text(sub_text, "utf-8")

    patches = (
        ("f4se/BSSkin.h", "#include <xmmintrin.h>"),
        ("f4se/PapyrusObjects.h", "#include <algorithm>"),
    )
    num_patches_applied = 0
    for file, line in patches:
        if add_include_line(ctx.f4se_dir / file, line):
            num_patches_applied += 1
    print(f"Patched: {num_patches_applied}/{len(patches)}")


def update_project_references(ctx: Context):
    """
    Replaces $(SolutionDir) in the reference paths of the supplied
    project file with the specified directory.
    The first vcxproj found in the project directory will be used.
    Creates build.vcxproj in the build directory.
    """
    ctx.src_project = next(ctx.project_dir.glob("*.vcxproj"))
    print(f"Project Path: {ctx.src_project}")

    # project_text = ctx.src_project.read_text("utf-8")
    # Update project references
    # sub_text = re.sub(
    #     r'(<ProjectReference Include=")'
    #     r'(\$\(SolutionDir\))'
    #     r'(.+">[\S\s]*?</ProjectReference>)',
    #     repl=rf"\1{str(ctx.f4se_dir).replace("\\", r"\\")}\3",
    #     string=project_text,
    #     flags=re.MULTILINE
    # )
    ctx.build_project = ctx.build_dir / "build.vcxproj"
    # ctx.build_project.write_text(sub_text, "utf-8")
    shutil.copy(ctx.src_project, ctx.build_project)


def make_solution(ctx: Context):
    """
    Creates a f4se_plugin.sln solution file to compile the specified project
    in the build directory.
    """
    # Regex Patterns
    re_project = re.compile(r'(Project\("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}"\) = ")PLUGIN_NAME(", ")PLUGIN_DIR(", "{00000000-0000-0000-0000-000000000000}"\nEndProject)', re.MULTILINE)
    re_guid = re.compile(r'(.*){00000000-0000-0000-0000-000000000000}(.*)')

    # Backreference 2 holds the values
    re_proj_name = re.compile(r'(<PropertyGroup.*Label="Globals">[\S\s]*?<RootNamespace>)(.*)(</RootNamespace>[\S\s]*?</PropertyGroup>)', re.MULTILINE)
    re_proj_guid = re.compile(r'(<PropertyGroup.*Label="Globals">[\S\s]*?<ProjectGuid>)(.*)(</ProjectGuid>[\S\s]*?</PropertyGroup>)', re.MULTILINE)

    project_text = ctx.src_project.read_text("utf-8")
    name_match = re_proj_name.search(project_text)
    guid_match = re_proj_guid.search(project_text)
    if not all((name_match, guid_match)):
        print("FATAL: Cannot extract name or GUID from project file.")
        raise SystemExit(1)
    plugin_name = name_match[2]
    plugin_guid = guid_match[2]

    # Read template sln file
    solution_text = (Path(__file__).parent / "f4se_plugin.sln").read_text("utf-8")

    # Update project references
    from pathlib import PureWindowsPath
    solution_text = re_project.sub(
        rf"\1{plugin_name}\2{str(ctx.build_project.relative_to(ctx.build_dir)).replace("\\", r"\\")}\3",
        solution_text)
    solution_text = re_guid.sub(rf"\1{plugin_guid}\2", solution_text)

    ctx.build_solution = ctx.build_dir / "f4se_plugin.sln"
    ctx.build_solution.write_text(solution_text, "utf-8")


def build_plugin(ctx: Context):
    try:
        subprocess.run(["msbuild", ctx.build_solution.relative_to(ctx.build_dir),
                        f"/p:PlatformToolset={ctx.platform_toolset}",
                        f'/p:IncludePath="{ctx.f4se_dir};{ctx.build_dir};{os.environ["INCLUDE"]}"',
                        "/p:UseEnv=true", "/p:Configuration=Release"],
                       cwd=ctx.build_dir,
                       check=True)
    except subprocess.CalledProcessError as e:
        raise SystemExit(e.returncode)


def package_plugin(ctx: Context):
    re_property = re.compile(r"""^#define\s+(\w+)\s+(?:"(.*)")|([\w.]+)$""")
    try:
        properties = {}
        with open(ctx.project_dir / "Config.h") as f:
            for line in f:
                if m := re_property.search(line):
                    properties[m[0]] = m[1] or m[2]
        archive_name = "{name} {version}".format(
            name=properties.get("PLUGIN_NAME_LONG") or properties["PLUGIN_NAME_SHORT"],
            version=properties.get("PLUGIN_VERSION_STRING")
        )
    except Exception:
        archive_name = "plugin"

    plugin_dll = next((ctx.build_dir / "x64/Release").glob("*.dll"))
    ctx.dist_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(ctx.dist_dir / f"{archive_name}.zip", "w") as zipf:
        print(f"Adding: {plugin_dll}")
        zipf.write(plugin_dll, f"Data/F4SE/Plugins/{plugin_dll}")
        if ctx.include_extras is not None:
            for dir_name, _, filenames in ctx.include_extras.walk():
                arch_dir = dir_name.relative_to(ctx.include_extras)
                if not filenames:
                    continue
                for filename in filenames:
                    print(f"Adding: {dir_name / filename}")
                    zipf.write(dir_name / filename, arch_dir / filename)
        print(f"Archive created at: {zipf.filename}")


if __name__ == "__main__":
    raise SystemExit(main())
