"""Version proof identity and source classification while excluding HTTP routes."""
import ast
from pathlib import Path
import unittest

import legal_audit_revision as revision


class ProofRetentionSignature(unittest.TestCase):
    def source(self, module):
        return (Path(__file__).resolve().parents[1] / 'scripts' / (module + '.py')).read_text()

    def canonical(self, module, source):
        return revision.canonical_engine_ast(source, revision.DEPENDENCY_REGISTRY[module], module)

    def test_proof_selection_and_current_source_boundaries_are_registered(self):
        expected = {
            'thematic_proof_variants': {'storage_key', 'source_sql', 'raw_rows'},
            'v384_thematic_findings': {'canonical_item', 'collapse_exact_claims', '_classify_sources'},
            'v385_msp_reassessment': {'apply_reassessments'},
        }
        for module, roots in expected.items():
            with self.subTest(module=module):
                self.assertTrue(roots.issubset(revision.DEPENDENCY_REGISTRY.get(module, ())))
                self.assertTrue(self.canonical(module, self.source(module)))

    def test_legal_ground_is_a_signed_field_of_retained_representations(self):
        module = 'thematic_proof_variants'
        source = self.source(module)
        tree = ast.parse(source)
        declaration = next(node for node in tree.body if isinstance(node, ast.Assign)
                           and any(isinstance(name, ast.Name) and name.id == 'FIELDS' for name in node.targets))
        declaration.value.elts = [value for value in declaration.value.elts
                                  if not isinstance(value, ast.Constant) or value.value != 'law']
        self.assertNotEqual(self.canonical(module, source), self.canonical(module, ast.unparse(tree)))

    def test_authority_rank_changes_the_shared_legal_signature(self):
        module = 'v384_thematic_findings'
        source = self.source(module)
        tree = ast.parse(source)
        group = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == 'collapse_exact_claims')
        rank = next(node for node in group.body if isinstance(node, ast.FunctionDef) and node.name == 'rank')
        rank.body = [ast.Return(value=ast.Constant(value=0))]
        self.assertNotEqual(self.canonical(module, source), self.canonical(module, ast.unparse(tree)))

    def test_http_recheck_route_does_not_enter_source_classification_signature(self):
        module = 'v385_msp_reassessment'
        source = self.source(module)
        tree = ast.parse(source)
        route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == 'recheck_msp')
        route.body = [ast.Return(value=ast.Constant(value='Changed route presentation'))]
        self.assertEqual(self.canonical(module, source), self.canonical(module, ast.unparse(tree)))


if __name__ == '__main__':
    unittest.main()
