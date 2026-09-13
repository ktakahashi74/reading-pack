from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support import cli, read_json


class PipelineContractCliTests(unittest.TestCase):
    def recipe(self, output: Path, *extra: str):
        return cli(
            'pipeline', 'recipe', '--experimental', '--output', str(output),
            '--generator', '/bin/false', '--judge', '/bin/false', '--reader', '/bin/false',
            '--generator-model', 'generator', '--judge-model', 'judge', '--reader-model', 'reader',
            *extra,
        )

    def test_recipe_without_contract_version_remains_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'recipe.json'
            result = self.recipe(output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('contract_version', read_json(output))

    def test_recipe_saves_each_explicit_supported_contract_version(self):
        for version in ('legacy-reader-evaluation-1', 'artifact-acceptance-1'):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'recipe.json'
                result = self.recipe(output, '--contract-version', version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(read_json(output)['contract_version'], version)

    def test_recipe_rejects_unknown_contract_version(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'recipe.json'
            result = self.recipe(output, '--contract-version', 'unknown-contract')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('invalid choice', result.stderr)
            self.assertFalse(output.exists())

    def test_artifact_recipe_omits_unused_reader_and_freezes_zero_repairs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'recipe.json'
            result = cli('pipeline','recipe','--experimental','--output',str(output),
                         '--generator','/bin/false','--generator-model','g',
                         '--judge','/bin/false','--judge-model','j',
                         '--contract-version','artifact-acceptance-1','--repair-rounds','0')
            self.assertEqual(result.returncode,0,result.stderr)
            recipe=read_json(output)
            self.assertEqual(recipe['max_rounds'],1)
            self.assertEqual(recipe['workers']['reader'],recipe['workers']['judge'])


if __name__ == '__main__':
    unittest.main()
