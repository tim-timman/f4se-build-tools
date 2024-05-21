# f4se-build-tools
Build script for F4SE plugins. The purpose of these scripts is to support automated builds of F4SE plugins in continuous integration services. These build tools prepare a clean development environment for plugin compilation.

## Requirements
- Visual Studio 2017
- Python 3.11+

## What it does
`build_plugin.py` is the entry point for the build tool.

The build tools do the following:
1. Fetch the specified revision of F4SE from [f4se](https://github.com/ianpatt/f4se) and [common](https://github.com/ianpatt/common).
2. Prepare the F4SE codebase for plugin compilation.
3. Generate a plugin project file (`build.vcxproj`) for compilation. 
4. Generate a solution file (`build.sln`) for command-line compilation with `msbuild`.
5. Builds the plugin and required F4SE components with `msbuild`.
