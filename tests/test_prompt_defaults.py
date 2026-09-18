"""Generated pipelines adapt DataFlow's class-valued prompt default."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from dataflow.core import LLMServingABC
from dataflow.prompts.reasoning.math import MathAnswerGeneratorPrompt
from dataflow_agents.catalog import discover_operator_catalog
from dataflow_agents.compiler import compile_spec, normalize_operator_defaults, render_dataflow_pipeline
from dataflow_agents.orchestrator import load_config
from dataflow_agents.prompt_templates import OPERATOR_PROMPTS, PROMPT_CLASSES, normalize_prompt, instantiate_prompt


class LocalServing(LLMServingABC):
    def __init__(self):
        self.prompts = []
    def generate_from_input(self, user_inputs, system_prompt=''):
        self.prompts.extend(user_inputs)
        return ['The answer is 2.'] * len(user_inputs)
    def start_serving(self): pass
    def cleanup(self): pass


class PromptDefaultTests(unittest.TestCase):
    def setUp(self):
        self.step = {'step_id': 'answer', 'operator': 'ReasoningAnswerGenerator',
                     'proposal': None, 'init_args': {'llm_serving': {'$resource': 'llm_default'}},
                     'prepare_fields': {}, 'run_args': {'input_key': 'question', 'output_key': 'answer'},
                     'depends_on': [], 'rationale': 'Answer questions'}
        self.spec = {'steps': [self.step], 'resources': {}, 'initial_keys': ['question'],
                     'final_keys': ['question', 'answer']}

    def test_old_spec_renders_and_executes_real_operator_without_remote_api(self):
        original = copy.deepcopy(self.spec)
        namespace = {'__name__': 'generated_test'}
        exec(compile(render_dataflow_pipeline(self.spec), '<generated>', 'exec'), namespace)
        serving = LocalServing()
        namespace['RESOURCE_CACHE']['llm_default'] = serving
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = root/'input.jsonl'
            input_file.write_text(json.dumps({'question': 'What is 1 + 1?'})+'\n')
            pipeline = namespace['GeneratedPipeline'](input_file, root/'cache')
            self.assertIsInstance(pipeline.op_0.prompts, MathAnswerGeneratorPrompt)
            pipeline.compile()
            pipeline.forward()
            rows = namespace['read_last'](pipeline).to_dict(orient='records')
        self.assertEqual(rows, [{'question': 'What is 1 + 1?', 'answer': 'The answer is 2.'}])
        self.assertIn('What is 1 + 1?', serving.prompts[0])
        self.assertEqual(self.spec, original)

    def test_compile_spec_includes_explicit_none(self):
        catalog = discover_operator_catalog(load_config()['dataflow_root'])
        plan = {'steps': [{'step_id': 'answer', 'depends_on': [], 'output_keys': ['answer']}],
                'final_keys': ['question', 'answer']}
        spec, errors = compile_spec(plan, {'bindings': [self.step], 'final_keys': plan['final_keys']},
                                    catalog, ['question'])
        self.assertEqual(errors, [])
        self.assertIn('prompt_template', spec['steps'][0]['init_args'])
        self.assertIsNone(spec['steps'][0]['init_args']['prompt_template'])

    def test_preserves_explicit_values_other_operators_and_custom_proposals(self):
        self.step['init_args']['prompt_template'] = 'explicit-invalid-choice'
        with self.assertRaisesRegex(ValueError, 'unsupported template'):
            normalize_operator_defaults(self.spec)
        del self.step['init_args']['prompt_template']
        self.step['proposal'] = {'source': 'custom'}
        self.assertEqual(normalize_operator_defaults(self.spec), self.spec)
        self.step['proposal'] = None
        self.step['operator'] = 'SomeOtherOperator'
        self.assertEqual(normalize_operator_defaults(self.spec), self.spec)

    def test_all_reasoning_templates_construct_real_operators(self):
        from dataflow.utils.registry import OPERATOR_REGISTRY
        for operator, names in OPERATOR_PROMPTS.items():
            for name in names:
                args = {'prompt_template': 'Evaluate {question}'} if name.startswith('Diy') else {}
                forms = [{'$prompt': name, 'args': args}]
                if not args:
                    forms += [name, name+'()', PROMPT_CLASSES[name]+'.'+name]
                for value in forms:
                    with self.subTest(operator=operator, template=value):
                        template = instantiate_prompt(operator, value)
                        instance = OPERATOR_REGISTRY.get(operator)(llm_serving=LocalServing(), prompt_template=template)
                        actual = getattr(instance, 'prompts', getattr(instance, 'prompt_template', None))
                        self.assertIs(actual, template)
                        self.assertEqual(type(template).__name__, name)
            with self.subTest(operator=operator, template=None):
                instance = OPERATOR_REGISTRY.get(operator)(llm_serving=LocalServing(), prompt_template=None)
                actual = getattr(instance, 'prompts', getattr(instance, 'prompt_template', None))
                self.assertEqual(type(actual).__name__, names[0])

    def test_rejects_unknown_wrong_operator_and_arbitrary_code(self):
        for value in ['MathAnswerGeneratorPrompt', '__import__("os").system("touch /tmp/unsafe")',
                      {'$prompt': 'os.system', 'args': {}}, {'$prompt': 'MathQuestionFilterPrompt', 'args': {'anything': 1}},
                      {'$prompt': 'DiyQuestionFilterPrompt'}, 'custom natural language prompt']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_prompt('ReasoningQuestionFilter', value)

    def test_filter_string_reference_executes_in_generated_pipeline(self):
        from unittest.mock import Mock
        spec = copy.deepcopy(self.spec)
        step = spec['steps'][0]
        step.update(operator='ReasoningQuestionFilter', run_args={'input_key': 'question'})
        step['init_args']['prompt_template'] = 'MathQuestionFilterPrompt'
        spec['final_keys'] = ['question']
        namespace = {'__name__': 'generated_test'}
        exec(compile(render_dataflow_pipeline(spec), '<generated>', 'exec'), namespace)
        serving = LocalServing()
        serving.generate_from_input = Mock(return_value=['{"judgement_test": true}'])
        namespace['RESOURCE_CACHE']['llm_default'] = serving
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file = root/'input.jsonl'
            input_file.write_text(json.dumps({'question': 'What is 1 + 1?'})+'\n')
            pipeline = namespace['GeneratedPipeline'](input_file, root/'cache')
            pipeline.compile()
            pipeline.forward()
            self.assertEqual(len(namespace['read_last'](pipeline)), 1)
        self.assertEqual(spec['steps'][0]['init_args']['prompt_template'], 'MathQuestionFilterPrompt')
