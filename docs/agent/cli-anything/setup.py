from setuptools import setup, find_namespace_packages

setup(
    name="cli-anything-asciicker-tools",
    version="1.1.0",
    description="CLI harnesses for Asciicker asciiid and Blender workflows",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-asciiid=cli_anything.asciiid.asciiid_cli:main",
            "cli-anything-blender=cli_anything.blender.blender_cli:main",
        ],
    },
    python_requires=">=3.10",
)
