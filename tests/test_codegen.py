"""Generated pipelines must read like the pipelines DataFlow ships."""
import ast
import json
import tempfile
import unittest
from pathlib import Path

from dataflow_agents.codegen import (RUNNER_FILENAME, literal, pipeline_class_name,
                                     render_dataflow_pipeline, write_pipeline_sources)


def step(**overrides):
    base = {'step_id': 'step_1', 'operator': 'RemoveExtraSpacesRefiner',
            'import_path': 'dataflow.operators.general_text',
            'module': 'dataflow.operators.general_text.refine.remove_extra_spaces_refiner',
            'proposal': None, 'init_args': {}, 'prepare_fields': {},
            'run_args': {'input_key': 'raw_content'}}
    base.update(overrides)
    return base


def spec(steps, **overrides):
    base = {'steps': steps, 'initial_keys': ['raw_content'], 'final_keys': ['raw_content'],
            'resources': {}, 'servings': {}}
    base.update(overrides)
    return base


class CodegenTests(unittest.TestCase):
    def test_source_is_plain_dataflow_without_workbench_runtime(self):
        source = render_dataflow_pipeline(spec([step()]), request='清理空格')
        tree = ast.parse(source)
        self.assertIn('from dataflow.operators.general_text import RemoveExtraSpacesRefiner', source)
        # The defining module is an implementation detail; DataFlow examples
        # always import from the operator package.
        self.assertNotIn('remove_extra_spaces_refiner import', source)
        # No embedded spec, no generic interpreter, no argparse harness.
        for leaked in ('SPEC = ', 'OPERATOR_REGISTRY', 'argparse', 'importlib', 'RESOURCE_CACHE'):
            self.assertNotIn(leaked, source)
        classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual(classes, ['GeneralText_CPUPipeline'])
        self.assertIn('    pipeline = GeneralText_CPUPipeline()\n    pipeline.compile()\n    pipeline.forward()', source)

    def test_operators_are_named_attributes_called_in_order(self):
        steps = [step(prepare_fields={'cleaned': 'raw_content'}),
                 step(step_id='step_2', operator='HashDeduplicateFilter',
                      init_args={'hash_func': 'md5'},
                      run_args={'input_key': 'cleaned', 'output_key': 'label'})]
        source = render_dataflow_pipeline(spec(steps))
        self.assertIn('self.copy_cleaned_step1 = CopyFieldRefiner()', source)
        self.assertIn('self.remove_extra_spaces_refiner_step1 = RemoveExtraSpacesRefiner()', source)
        self.assertIn('self.hash_deduplicate_filter_step2 = HashDeduplicateFilter(\n            hash_func="md5",\n        )', source)
        called = [line.strip() for line in source.splitlines() if line.strip().startswith('self.') and '.run(' in line]
        self.assertEqual(called, ['self.copy_cleaned_step1.run(',
                                  'self.remove_extra_spaces_refiner_step1.run(',
                                  'self.hash_deduplicate_filter_step2.run('])
        self.assertEqual(source.count('storage=self.storage.step(),'), 3)

    def test_serving_is_constructed_inline_and_referenced_by_attribute(self):
        resources = {'llm_default': {'type': 'api_llm', 'args': {
            'api_url': 'https://api.example.com/v1', 'model_name': 'gpt-4o',
            'key_name_of_api_key': 'DF_PIPELINE_LLM', 'max_workers': 4}}}
        generator = step(step_id='step_1', operator='ReasoningAnswerGenerator',
                         import_path='dataflow.operators.reasoning',
                         init_args={'llm_serving': {'$resource': 'llm_default'}},
                         run_args={'input_key': 'question', 'output_key': 'answer'})
        source = render_dataflow_pipeline(spec([generator], resources=resources, servings=resources))
        self.assertIn('from dataflow.serving import APILLMServing_request', source)
        self.assertIn('api_url="https://api.example.com/v1/chat/completions"', source)
        self.assertIn('self.llm_default = APILLMServing_request(', source)
        self.assertIn('llm_serving=self.llm_default,', source)
        self.assertEqual(pipeline_class_name(spec([generator], servings=resources)), 'Reasoning_APIPipeline')

    def test_unresolvable_references_fail_loudly(self):
        dangling = step(init_args={'llm_serving': {'$resource': 'missing'}})
        with self.assertRaisesRegex(ValueError, 'undeclared serving resource'):
            render_dataflow_pipeline(spec([dangling]))
        nameless = step()
        del nameless['import_path'], nameless['module']
        with self.assertRaisesRegex(ValueError, 'no operator module'):
            render_dataflow_pipeline(spec([nameless]))

    def test_generated_operator_is_imported_from_the_run_package(self):
        proposal = {'name': 'CountUpperA', 'source': 'class CountUpperA: pass\n', 'tests': []}
        generated = step(operator='CountUpperA', proposal=proposal, import_path='custom.CountUpperA',
                         source_file='custom/CountUpperA.py', module='custom.CountUpperA')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_pipeline_sources(root, spec([generated]), 'count letters')
            source = (root / 'pipeline.py').read_text()
            self.assertIn('from custom.CountUpperA import CountUpperA', source)
            self.assertTrue((root / 'custom/__init__.py').exists())
            self.assertEqual((root / 'custom/CountUpperA.py').read_text(), proposal['source'])
            runner = (root / RUNNER_FILENAME).read_text()
            self.assertIn('def guard_servings', runner)
            ast.parse(runner)

    def test_values_render_as_reviewable_python_literals(self):
        self.assertEqual(literal('a"b'), '"a\\"b"')
        self.assertEqual(literal([1, 2]), '[1, 2]')
        self.assertEqual(literal({'a': None, 'b': True}), '{"a": None, "b": True}')
        nested = {'keys': ['x'] * 30}
        self.assertIn('\n', literal(nested))
        self.assertEqual(ast.literal_eval(literal(nested)), json.loads(json.dumps(nested)))
