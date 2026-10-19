"""Engine revisions describe executable legal inputs, not deployments or UI."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import legal_audit_revision as revision


class LegalEngineSignature(unittest.TestCase):
    def setUp(self):
        revision.engine_signature.cache_clear()
        self.addCleanup(revision.engine_signature.cache_clear)

    def fingerprint(self, source, roots=("analyze",)):
        return revision.canonical_engine_ast(source, roots)

    def test_comments_docstrings_spacing_and_logs_are_cosmetic(self):
        first='''"""Module documentation."""
LIMIT = 3

def analyze(value):
    """Explain the original rule."""
    # Qualified original evidence only.
    print("old diagnostic")
    return value > LIMIT
'''
        second='''# different comment
"""New module documentation."""
LIMIT=3

def analyze(value):
    """New explanation."""
    print("new log, no rule change")
    return value>LIMIT
'''
        self.assertEqual(self.fingerprint(first), self.fingerprint(second))

    def test_substantive_rule_or_dependency_change_invalidates(self):
        original='LIMIT=3\ndef helper(value): return value > LIMIT\ndef analyze(value): return helper(value)\n'
        baseline=self.fingerprint(original)
        self.assertNotEqual(baseline,self.fingerprint(original.replace('LIMIT=3','LIMIT=4')))
        self.assertNotEqual(baseline,self.fingerprint(original.replace('value > LIMIT','value >= LIMIT')))

    def test_mutable_rule_initialization_and_order_are_substantive(self):
        original='RULES={}\nRULES.update({"limit":3})\nRULES.update({"limit":4})\ndef analyze(value): return value > RULES["limit"]\n'
        baseline=self.fingerprint(original)
        self.assertNotEqual(baseline,self.fingerprint(original.replace('"limit":4','"limit":5')))
        reordered=original.replace('RULES.update({"limit":3})\nRULES.update({"limit":4})',
                                   'RULES.update({"limit":4})\nRULES.update({"limit":3})')
        self.assertNotEqual(baseline,self.fingerprint(reordered))
        self.assertEqual(baseline,self.fingerprint(original+'\nUI={}\nUI.update({"color":"blue"})\n'))
        repeated='RULES=[]\nRULES.append(1)\nRULES.append(1)\ndef analyze(value): return len(RULES) > value\n'
        self.assertNotEqual(self.fingerprint(repeated),self.fingerprint(repeated.replace('RULES.append(1)\n','',1)))
        augmented='LIMIT=3\nLIMIT+=1\ndef analyze(value): return value > LIMIT\n'
        self.assertNotEqual(self.fingerprint(augmented),self.fingerprint(augmented.replace('LIMIT+=1','LIMIT+=2')))

    def test_ui_and_scheduler_definitions_are_excluded(self):
        original='LIMIT=3\nUI="old"\ndef render(): return UI\ndef tick(): return 2\ndef analyze(value): return value > LIMIT\n'
        changed=original.replace('UI="old"','UI="new"').replace('return 2','return 100')
        self.assertEqual(self.fingerprint(original),self.fingerprint(changed))

    def test_nested_legal_wrapper_does_not_include_neighbor_route(self):
        original='''def install(ns):
    def build_checks(value): return value + 1
    def page(): return "old page"
'''
        roots=("install.build_checks",)
        self.assertEqual(self.fingerprint(original,roots),self.fingerprint(original.replace('old page','new page'),roots))
        self.assertNotEqual(self.fingerprint(original,roots),self.fingerprint(original.replace('value + 1','value + 2'),roots))

    def test_operational_constant_exclusion_keeps_all_outer_evidence_gates(self):
        original='''WORKERS=12
def stored_docs(values):
    def read_one(item): return item > 3
    return [item for item in values if read_one(item)], WORKERS
def analyze(values): return stored_docs(values)
'''
        def fingerprint(value):
            return revision.canonical_engine_ast(value,('analyze',),ignored_dependencies={'WORKERS'})
        baseline=fingerprint(original)
        self.assertEqual(baseline,fingerprint(original.replace('WORKERS=12','WORKERS=6')))
        self.assertNotEqual(baseline,fingerprint(original.replace('item > 3','item > 4')))
        self.assertNotEqual(baseline,fingerprint(original.replace('if read_one(item)','if item and read_one(item)')))

    def test_missing_registry_root_and_invalid_python_fail_explicitly(self):
        with self.assertRaises(revision.EngineSignatureError):self.fingerprint('def other(): pass')
        with self.assertRaises(revision.EngineSignatureError):self.fingerprint('def analyze(')

    def test_full_signature_is_deterministic_and_cached(self):
        first=revision.engine_signature()
        self.assertRegex(first,r'^[0-9a-f]{64}$')
        with patch.object(revision,'_read_engine',side_effect=AssertionError('cached call must not read')):
            self.assertEqual(first,revision.engine_signature())
        revision.engine_signature.cache_clear()
        self.assertEqual(first,revision.engine_signature())

    def test_changed_substantive_module_changes_process_signature(self):
        baseline=revision.engine_signature();revision.engine_signature.cache_clear()
        original=revision._read_engine
        def changed(path):
            value=original(path)
            return value.replace('MAX_DOCS=500','MAX_DOCS=501') if path.name=='v331_broad_legal.py' else value
        with patch.object(revision,'_read_engine',changed):
            self.assertNotEqual(baseline,revision.engine_signature())

    def test_unrelated_mixed_module_changes_do_not_change_signature(self):
        baseline=revision.engine_signature();revision.engine_signature.cache_clear()
        original=revision._read_engine
        def changed(path):
            value=original(path)
            if path.name=='v332_all_procurements_audit.py':
                value=value.replace('QUEUE_POLL_SECONDS=30.0','QUEUE_POLL_SECONDS=700.0')
                self.assertIn('FILE_BATCH_SIZE=12',value)
                value=value.replace('FILE_BATCH_SIZE=12','FILE_BATCH_SIZE=6')
            if path.name=='v333_dedup_ui.py':
                value+='\ndef brand_new_ui_route():\n    return "new interface"\n'
            return value
        with patch.object(revision,'_read_engine',changed):
            self.assertEqual(baseline,revision.engine_signature())

    def test_installed_analyzer_registry_covers_every_startup_wrapper(self):
        folder=Path(revision.__file__).parent
        startup=ast.parse((folder/'railway_start_v282.py').read_text())
        installed={node.func.value.id for node in ast.walk(startup) if isinstance(node,ast.Call)
            and isinstance(node.func,ast.Attribute) and node.func.attr=='install' and isinstance(node.func.value,ast.Name)}
        wrappers=set()
        for name in installed:
            path=folder/(name+'.py')
            if not path.exists():continue
            tree=ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Subscript) and isinstance(node.ctx,ast.Store) and isinstance(node.value,ast.Name) and node.value.id=='ns':
                    if isinstance(node.slice,ast.Constant) and node.slice.value in ('analyze','build_checks'):
                        wrappers.add(name)
        self.assertTrue(wrappers)
        self.assertEqual(wrappers,set(revision.ANALYZER_REGISTRY))

    def test_source_evidence_and_dedup_dependencies_are_registered(self):
        required={'legal_applicability','official_publication_evidence','eis223_soap_xml',
                  'v295_eis_originals','v298_eis_only','v310_archive_scan','v311_verified_customer',
                  'v314_eis_metadata','v324_official_price','v332_all_procurements_audit',
                  'v335_price_recovery','v341_eis_facts','v342_thematic_queue',
                  'v347_text_cache','v348_cross_document_legal','v352_dashboard_truth','v353_episode_guard','v383_msp_scope'}
        self.assertTrue(required <= set(revision.DEPENDENCY_REGISTRY))
        self.assertIn('collect_saved_evidence',revision.DEPENDENCY_REGISTRY['v332_all_procurements_audit'])
        self.assertIn('saved_review_context',revision.DEPENDENCY_REGISTRY['v298_eis_only'])
        self.assertIn('official_notice_record',revision.DEPENDENCY_REGISTRY['v298_eis_only'])
        self.assertIn('card_predates_notice',revision.DEPENDENCY_REGISTRY['v341_eis_facts'])
        self.assertIn('reconcile_notice_context',revision.DEPENDENCY_REGISTRY['v341_eis_facts'])
        self.assertIn('event_occurrence_text',revision.DEPENDENCY_REGISTRY['v348_cross_document_legal'])
        self.assertNotIn('v345_thematic_accelerator',revision.DEPENDENCY_REGISTRY)
        self.assertNotIn('v381_audit_scheduling',revision.DEPENDENCY_REGISTRY)

    def test_explicit_legacy_rule_revisions_are_shared_engine_inputs(self):
        baseline=revision.engine_signature()
        original=revision._read_engine
        for module,constant in (("v332_all_procurements_audit.py","RULE_REVISION"),
                                ("v345_thematic_accelerator.py","ANALYSIS_VERSION")):
            revision.engine_signature.cache_clear()
            def changed(path):
                value=original(path)
                if path.name==module:
                    tree=ast.parse(value)
                    node=next(node for node in tree.body if isinstance(node,ast.Assign)
                              and any(isinstance(target,ast.Name) and target.id==constant for target in node.targets))
                    previous=node.value.value
                    node.value=ast.Constant(previous+1 if isinstance(previous,int) else previous+'-explicit-test')
                    value=ast.unparse(tree)
                return value
            with patch.object(revision,'_read_engine',changed):
                self.assertNotEqual(baseline,revision.engine_signature(),constant)
        self.assertEqual(revision.DECLARED_REVISION_REGISTRY["v345_thematic_accelerator"],("ANALYSIS_VERSION",))

    def test_concurrent_legal_source_and_rule_dependencies_change_signature(self):
        baseline=revision.engine_signature()
        original=revision._read_engine
        changes=(
            ('v341_eis_facts.py','capture<notice','capture<=notice'),
            ('v341_eis_facts.py','not equal(key,value,card_value) and not older',
                                  'not equal(key,value,card_value)'),
            ('v336_legal_matrix.py','if "price" in (p.get("_official_context_conflicts") or {}):return None',
                                    'if False:return None'),
            ('v324_official_price.py','RUBLE_TOKEN=r"руб(?:ль|ля|лей)?','RUBLE_TOKEN=r"руб(?:ль|ля)?'),
            ('v348_cross_document_legal.py','text,count=1) if pattern else text','text,count=2) if pattern else text'),
            ('v332_all_procurements_audit.py','bool(received_uids-seen_uids)','bool(received_uids)'),
            ('v332_all_procurements_audit.py','output=[doc for doc in results if doc is not None]',
                                            'output=[doc for doc in results if doc is not None and doc.get("status")=="checked"]'),
            ('v336_legal_matrix.py','WRITTEN_NUMBERS.update({"одна":1,"одно":1,"две":2})',
                                    'WRITTEN_NUMBERS.update({"одна":1,"одно":1,"две":3})'),
        )
        for module,before,after in changes:
            with self.subTest(module=module):
                revision.engine_signature.cache_clear()
                def changed(path):
                    value=original(path)
                    if path.name==module:
                        self.assertIn(before,value)
                        value=value.replace(before,after,1)
                    return value
                with patch.object(revision,'_read_engine',changed):
                    self.assertNotEqual(baseline,revision.engine_signature())

    def test_engine_read_failure_is_not_cached(self):
        with patch.object(revision,'_read_engine',side_effect=revision.EngineSignatureError('unavailable')):
            with self.assertRaises(revision.EngineSignatureError):revision.engine_signature()
        self.assertEqual(len(revision.engine_signature()),64)

    def test_partial_soap_acceptance_and_completeness_are_engine_inputs(self):
        baseline=revision.engine_signature()
        original=revision._read_engine
        for module,function in (("v294_eis_bridge.py","parse_notices"),
                                ("v294_eis_bridge.py","soap_metadata_incomplete"),
                                ("eis223_soap_xml.py","quarantine_document")):
            with self.subTest(module=module,function=function):
                revision.engine_signature.cache_clear()
                def changed(path):
                    source=original(path)
                    if path.name!=module:return source
                    tree=ast.parse(source)
                    target=next(node for node in tree.body
                                if isinstance(node,ast.FunctionDef) and node.name==function)
                    # A changed acceptance/completeness result must invalidate
                    # existing audits even without an explicit version bump.
                    offset=int(isinstance(target.body[0],ast.Expr) and
                               isinstance(target.body[0].value,ast.Constant) and
                               isinstance(target.body[0].value.value,str))
                    target.body.insert(offset,ast.Return(ast.Constant(None)))
                    return ast.unparse(tree)
                with patch.object(revision,'_read_engine',changed):
                    self.assertNotEqual(baseline,revision.engine_signature())


if __name__ == '__main__': unittest.main()
