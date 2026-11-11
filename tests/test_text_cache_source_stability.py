"""A cached excerpt must still describe the current immutable source bytes."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import v347_text_cache as cache


class SourceStability(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='eis-cache-stability-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'source.pdf'
        self.path.write_bytes(b'original evidence bytes')
        self.temp = patch.object(cache.tempfile, 'gettempdir', return_value=temporary.name)
        self.temp.start()
        self.addCleanup(self.temp.stop)

    def extract(self, parser):
        return cache.cached_extract(self.path, parser, data_root=self.root)

    def test_replacement_after_hash_does_not_release_cached_text(self):
        self.assertEqual(self.extract(lambda _: 'old payment terms'), 'old payment terms')
        digest = hashlib.file_digest
        def change(stream, algorithm):
            value = digest(stream, algorithm)
            self.path.write_bytes(b'revised evidence bytes')
            return value
        with patch.object(cache.hashlib, 'file_digest', side_effect=change):
            self.assertEqual(self.extract(lambda _: self.fail('Changed source must await a new verified pass')), '')
        self.assertEqual(self.extract(lambda _: 'revised payment terms'), 'revised payment terms')

    def test_replacement_during_parser_does_not_release_unbound_evidence(self):
        def parser(_):
            self.path.write_bytes(b'revised evidence bytes')
            return 'text not bound to the original hash'
        self.assertEqual(self.extract(parser), '')
        self.assertEqual(self.extract(lambda _: 'verified revised terms'), 'verified revised terms')

    def test_disappearing_source_during_parser_remains_incomplete(self):
        def parser(_):
            self.path.unlink()
            return 'unverifiable text'
        self.assertEqual(self.extract(parser), '')

    def test_unchanged_cache_still_reuses_parser_output(self):
        self.assertEqual(self.extract(lambda _: 'verified terms'), 'verified terms')
        self.assertEqual(self.extract(lambda _: self.fail('Unexpected repeated parsing')), 'verified terms')

    def test_retargeted_source_alias_cannot_poison_resolved_content_key(self):
        alias=self.root/'alias.pdf'
        other=self.root/'other.pdf'
        other.write_bytes(b'replacement evidence')
        alias.symlink_to(self.path)
        def parser(supplied):
            alias.unlink()
            alias.symlink_to(other)
            return Path(supplied).read_text()
        self.assertEqual(cache.cached_extract(alias,parser,data_root=self.root),'')
        self.assertEqual(self.extract(lambda source: Path(source).read_text()),'original evidence bytes')

    def test_retargeted_alias_during_cache_hit_does_not_release_old_text(self):
        self.assertEqual(self.extract(lambda _: 'old payment terms'),'old payment terms')
        alias=self.root/'alias.pdf'
        other=self.root/'other.pdf'
        other.write_bytes(b'replacement evidence')
        alias.symlink_to(self.path)
        digest=hashlib.file_digest
        def change(stream,algorithm):
            value=digest(stream,algorithm)
            alias.unlink()
            alias.symlink_to(other)
            return value
        with patch.object(cache.hashlib,'file_digest',side_effect=change):
            self.assertEqual(cache.cached_extract(alias,lambda _: 'unexpected',data_root=self.root),'')
