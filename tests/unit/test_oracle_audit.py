"""Oracle review questions expose assumptions without deciding their semantics."""
from reproagent.core.assertion_audit import assertion_sites


def scan(content, extra=()):
    from reproagent.core.oracle_audit import oracle_checks
    files = [{'path': 'testing/test_case.py', 'role': 'test', 'content': content}, *extra]
    return oracle_checks(files)


def test_simple_value_assertion_does_not_expand_review_protocol():
    assert scan('from library import parse\ndef test_case():\n    assert parse([]) == []\n') == []


def test_fnmatch_fixed_fragments_and_composition_have_separate_questions():
    rows = scan('def test_case(result):\n'
                '    result.stdout.fnmatch_lines(["*obsolete phrase*new_option=True*"], consecutive=False)\n')
    fragments = [row for row in rows if row['kind'] == 'pattern_fragment']
    assert [row['details']['text'] for row in fragments] == ['obsolete phrase', 'new_option=True']
    composition = next(row for row in rows if row['kind'] == 'pattern_composition')
    assert composition['details']['same_line'] is True
    assert composition['details']['ordered_fragments'] is True
    assert {row['assertion_id'] for row in rows} == {'A1'}
    assert all('invalid' not in row for row in rows)


def test_helper_selector_and_window_are_audited_at_their_actual_locations():
    rows = scan('def select(text, name):\n'
                '    lines = text.splitlines()\n'
                '    for index, line in enumerate(lines):\n'
                '        if line.strip() == name:\n'
                '            return "\\n".join(lines[index:index + 4])\n'
                '    return ""\n'
                'def test_case(output):\n'
                '    entry = select(output, "alpha")\n'
                '    assert entry\n')
    predicates = [row for row in rows if row['kind'] == 'helper_predicate']
    windows = [row for row in rows if row['kind'] == 'helper_selection']
    assert len(predicates) == 1 and predicates[0]['start_line'] == 4
    assert 'line.strip() == name' in predicates[0]['expression']
    assert len(windows) == 1 and windows[0]['start_line'] == 5
    assert all(row['assertion_id'] == 'A1' for row in rows)


def test_loop_bound_expected_word_overlaps_generated_docstring():
    content = r'''import re
SOURCE = '\nimport pytest\n@pytest.fixture(scope="module")\ndef alpha():\n    """module description"""\n'
def test_case(output):
    for name, scope in (("alpha", "module"), ("beta", "session")):
        assert re.search(r"\b%s\b" % scope, output)
'''
    rows = scan(content)
    overlap = [row for row in rows if row['kind'] == 'candidate_text_overlap']
    assert len(overlap) == 1
    assert overlap[0]['details']['word'] == 'module'
    assert overlap[0]['details']['input_line'] == 2
    assert overlap[0]['details']['source_kind'] == 'embedded_metadata'


def test_dynamic_matcher_and_unknown_local_helper_cannot_claim_complete():
    rows = scan('def select(text):\n'
                '    return transform(text)\n'
                'def test_case(result, pattern):\n'
                '    result.stdout.fnmatch_lines(pattern)\n'
                '    assert select(result)\n')
    unknown = [row for row in rows if row['kind'] == 'analysis_unknown']
    assert {row['assertion_id'] for row in unknown} == {'A1', 'A2'}
    assert any('matcher' in row['details']['reason'] for row in unknown)
    assert any('helper' in row['details']['reason'] for row in unknown)


def test_assertion_ids_match_inventory_and_order_is_repeatable():
    content = ('def test_case(result):\n'
               '    assert result.ret == 0\n'
               '    result.stdout.fnmatch_lines(["*first*second*"])\n')
    rows = scan(content)
    sites = assertion_sites([{'path': 'testing/test_case.py', 'role': 'test', 'content': content}])
    assert {row['assertion_id'] for row in rows} == {sites[1]['id']}
    assert [row['id'] for row in rows] == [f'O{i}' for i in range(1, len(rows) + 1)]
    assert scan(content) == rows


def test_long_details_are_bounded_and_invalid_data_python_is_not_parsed():
    rows = scan('def test_case(result):\n    result.stdout.fnmatch_lines(["*' + 'x' * 2000 + '*tail*"])\n',
                [{'path': 'bad.py', 'role': 'data', 'content': 'def broken(:'}])
    assert rows
    assert all(len(row['expression']) <= 240 for row in rows)
    assert all(len(value) <= 240 for row in rows for value in row['details'].values() if isinstance(value, str))
    assert any(row['kind'] == 'analysis_unknown' for row in rows)


def test_neutral_embedded_metadata_does_not_report_overlap():
    content = r'''import re
SOURCE = '\nimport pytest\n@pytest.fixture(scope="module")\ndef alpha():\n    """neutral description"""\n'
def test_case(output):
    assert re.search("module", output)
'''
    assert not any(row['kind'] == 'candidate_text_overlap' for row in scan(content))


def test_indented_embedded_python_and_fnmatch_literals_expose_overlap():
    content = '''def test_case(result):
    result.makepyfile('    def alpha():\\n        """special detail"""\\n')
    result.stdout.fnmatch_lines(["*special*"])
'''
    assert any(row['kind'] == 'candidate_text_overlap' for row in scan(content))


def test_recursive_and_dynamic_oracle_helper_report_unknown():
    content = ('def select(output):\n'
               '    return select(output)\n'
               'def test_case(output):\n'
               '    assert select(output)\n'
               '    assert factory()(output)\n')
    rows = scan(content)
    assert {row['assertion_id'] for row in rows if row['kind'] == 'analysis_unknown'} == {'A1', 'A2'}


def test_literal_pattern_anchors_and_line_sequence_are_not_silently_ignored():
    rows = scan('def test_case(result):\n'
                '    result.stdout.fnmatch_lines(["header*", "*detail"], consecutive=True)\n')
    compositions = [row['details'] for row in rows if row['kind'] == 'pattern_composition']
    assert compositions[0]['anchored_start'] is True
    assert compositions[1]['anchored_end'] is True
    sequence = next(row for row in rows if row['kind'] == 'matcher_sequence')
    assert sequence['details'] == {'pattern_count': 2, 'ordered_lines': True, 'consecutive': True}


def test_literal_input_overlap_is_a_question_even_for_legitimate_echo_behavior():
    rows = scan('def test_case():\n'
                '    output = render("marker")\n'
                '    assert "marker" in output\n')
    overlap = [row for row in rows if row['kind'] == 'candidate_text_overlap']
    assert len(overlap) == 1
    assert overlap[0]['details']['source_kind'] == 'input_literal'
    assert overlap[0]['details']['word'] == 'marker'


def test_dynamic_sequence_option_is_unknown_rather_than_invented_false():
    rows = scan('def test_case(result, option):\n'
                '    result.stdout.fnmatch_lines(["a", "b"], consecutive=option)\n')
    sequence = next(row for row in rows if row['kind'] == 'matcher_sequence')
    assert sequence['details']['consecutive'] is None
    assert any(row['kind'] == 'analysis_unknown' for row in rows)
