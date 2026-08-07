from pathlib import Path
from shutil import copyfile, copytree

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        source = Path(__file__).parent / "docs" / "images" / "logo.png"
        target = Path(self.build_lib) / "vidxp" / "assets" / "icon.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(source, target)

        plugin_source = Path(__file__).parent / "plugins" / "vidxp"
        plugin_target = Path(self.build_lib) / "vidxp" / "bundled_plugins" / "vidxp"
        copytree(plugin_source, plugin_target, dirs_exist_ok=True)
        copyfile(source, plugin_target / "assets" / "logo.png")


setup(cmdclass={"build_py": BuildPy})
