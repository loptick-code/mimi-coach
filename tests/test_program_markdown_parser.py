import unittest
from datetime import date
from app.services.program_markdown_parser import ProgramMarkdownError, parse_program_markdown
class ProgramMarkdownParserTests(unittest.TestCase):
    def test_normalizes_clean_markdown(self):
        parsed = parse_program_markdown('''# Programme Mi Coach\n\n<!-- MI_COACH_PROGRAM_VERSION: 1 -->\n\n## Première semaine\n- Date : 23/11/2026\n\n## Semaine 1\n### Séance 1\n- Type : EF\n- Description : 40 min EF\n\n### Séance 2\n- Type : fractionné\n- Description : 6 x (3 min S60 + 1 min récupération)\n''')
        self.assertEqual(parsed.start_date, date(2026, 11, 23))
        self.assertIn('Séance 2 : 6 x', parsed.normalized_text)
    def test_requires_version_marker(self):
        with self.assertRaises(ProgramMarkdownError): parse_program_markdown('## Première semaine\n- Date : 23/11/2026')
    def test_requires_monday(self):
        with self.assertRaises(ProgramMarkdownError): parse_program_markdown('''<!-- MI_COACH_PROGRAM_VERSION: 1 -->\n## Première semaine\n- Date : 24/11/2026\n## Semaine 1\n### Séance 1\n- Description : 40 min EF\n''')
if __name__ == '__main__': unittest.main()
