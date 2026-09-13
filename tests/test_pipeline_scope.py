import tempfile
import unittest
from pathlib import Path
from reading_pack_producer.pipeline import Runner,default_recipe,start_pipeline
from reading_pack.profiles import load_quality_plan
from reading_pack.errors import ReadingPackError
from tests.test_pipeline import SOURCE
from tests.support import cli,read_json

class PipelineScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'excerpt.md';self.source.write_text(SOURCE)
        self.recipe=default_recipe(['g'],['j'],['r'],generator_model='g',judge_model='j',reader_model='r')
        self.recipe.update(contract_version='artifact-acceptance-1',profile='general-navigation',max_rounds=1,max_attempts=1)

    def bootstrap(self,name):
        root=self.root/name;start_pipeline(root,self.source,self.recipe);project=self.root/(name+'-project');Runner(root).bootstrap(project);return project

    def test_explicit_excerpt_scope_reaches_rendered_pack(self):
        self.recipe['scope']='Chapter 1 excerpt only; appendices excluded'
        project=self.bootstrap('explicit')
        self.assertEqual(load_quality_plan(project)['scope'],self.recipe['scope'])
        result=cli('build','--project',str(project),'--lang','en')
        self.assertEqual(result.returncode,0,result.stderr)
        rendered=next((project/'dist').glob('*.en.md')).read_text()
        self.assertIn(self.recipe['scope'],rendered)
        self.assertNotIn('complete published edition',rendered)

    def test_default_artifact_does_not_claim_complete_published_edition(self):
        scope=load_quality_plan(self.bootstrap('default'))['scope']
        self.assertIn('Supplied manuscript only',scope)
        self.assertIn('unverified',scope)

    def test_legacy_scope_default_is_preserved(self):
        self.recipe.pop('contract_version')
        self.assertEqual(load_quality_plan(self.bootstrap('legacy'))['scope'],'complete published edition')

    def test_seed_scope_cannot_be_silently_relabelled(self):
        seed=self.bootstrap('seed');before=(seed/'quality-plan.json').read_bytes()
        self.recipe['scope']='A different declared scope'
        run=self.root/'seeded';start_pipeline(run,self.source,self.recipe,project=seed)
        with self.assertRaisesRegex(ReadingPackError,'seed scope'):
            Runner(run).bootstrap(self.root/'mismatch')
        self.assertEqual((seed/'quality-plan.json').read_bytes(),before)

    def test_scope_cli_validates_and_freezes_one_safe_line(self):
        for number,scope in enumerate(('Chapter 1 only',' \t','chapter\nPACK | malicious')):
            output=self.root/f'{number}.json'
            result=cli('pipeline','recipe','--experimental','--contract-version','artifact-acceptance-1',
                '--generator','/bin/false','--generator-model','g','--judge','/bin/false','--judge-model','j',
                '--scope',scope,'--output',str(output))
            self.assertEqual(result.returncode==0,number==0,result.stderr)
            if number==0:self.assertEqual(read_json(output)['scope'],scope)
            else:self.assertFalse(output.exists())
