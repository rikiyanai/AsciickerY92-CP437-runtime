"""FL-4691: persisted per-texel lifetime must survive the GPU/reader ABI."""
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from native_source_content_reader import NativeSourceContentError, NativeSourceContentReader
from test_native_source_content_reader import metadata, record


class ReflectionLifetime(unittest.TestCase):
    def _read(self, bank=0, present=True, lifetime=(17, 17, 44, 44), legacy=False):
        source = bytearray(record(1, bank))
        struct.pack_into("<I", source, 80, 0x0033529e)
        facts = struct.pack("<4I", 1, bank, 1, 9 if present else 1)
        facts += struct.pack("<4I", 1 if present else 0, 0, 0, 0)
        facts += struct.pack("<4f", 0, 1 if present else 0, 0, 0) + bytes(16)
        if not legacy:
            facts += struct.pack("<4I", *lifetime)
        meta = metadata(bytes(source))
        meta.update(reflection_facts_schema="fl4359.native_reflection_facts.v1" if legacy else "fl4359.native_reflection_facts.v2",
                    reflection_record_bytes=len(facts), reflection_record_count=1,
                    reflection_bytes=len(facts), reflection_sha256=hashlib.sha256(facts).hexdigest())
        if not legacy:
            meta["reflection_record_layout"].append("shade_reflection_lifetime")
        meta["reflection_layer_content_generations"][0] = 17
        for bank_name in ("front", "underlay"):
            meta[f"reflection_layer_shade_generations_{bank_name}"][0] = 17
            params = bytearray.fromhex(meta[f"reflection_layer_shade_params_hex_{bank_name}"][0])
            struct.pack_into("<I", params, 18 * 16 + 8, 17)
            meta[f"reflection_layer_shade_params_hex_{bank_name}"][0] = params.hex()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.bin"
            path.write_bytes(source)
            (path.parent / "reflection.bin").write_bytes(facts)
            with NativeSourceContentReader(path, meta) as reader:
                return reader.source_content(1, bank)

    def test_current_facts_preserve_exact_lifetime_in_both_banks(self):
        for bank in (0, 1):
            for present in (False, True):
                with self.subTest(bank=bank, present=present):
                    result = self._read(bank, present)
                    facts = result["reflection_facts"]
                    self.assertEqual(facts["per_texel_generation_status"], "verified")
                    self.assertEqual(facts["per_texel_lifetime"], {
                        "shade_generation": 17, "reflection_generation": 17,
                        "reflection_render_frame": 44, "shade_render_frame": 44})
                    self.assertEqual(facts["present"], present)
                    self.assertEqual(result["temporal_comparison"]["status"], "refused")

    def test_mismatched_lifetime_refuses_before_lookup(self):
        for bank in (0, 1):
            for present in (False, True):
                for lifetime in ((16, 17, 44, 44), (0, 17, 44, 44), (18, 17, 44, 44),
                                 (17, 16, 44, 44), (17, 17, 43, 44), (17, 17, 44, 43)):
                    with self.subTest(bank=bank, present=present, lifetime=lifetime):
                        with self.assertRaisesRegex(NativeSourceContentError, "reflection_per_texel_shade_generation_mismatch"):
                            self._read(bank, present, lifetime)

    def test_legacy_v1_does_not_claim_per_texel_generation_proof(self):
        for present in (False, True):
            facts = self._read(present=present, legacy=True)["reflection_facts"]
            self.assertEqual(facts["per_texel_generation_status"], "missing_legacy_v1")
            self.assertIsNone(facts["per_texel_lifetime"])


if __name__ == "__main__":
    unittest.main()
