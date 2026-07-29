from pathlib import Path
from shutil import copyfile

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        source = Path(__file__).parent / "docs" / "images" / "logo.png"
        target = Path(self.build_lib) / "vidxp" / "assets" / "icon.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(source, target)


setup(cmdclass={"build_py": BuildPy})
