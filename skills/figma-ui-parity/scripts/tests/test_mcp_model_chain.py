"""真实转换→提取→比较链路，未知字段不得变成确定缺陷。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

S = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(S))
import figma_mcp_to_json as conv
import extract_design_fields as extract
import compare_fields as compare
import design_contrast

SAMPLE = '''NAME: "Demo"
GLOBAL_VARS:
fill_bg: ['#FFFFFF']
ELEMENTS:
EL-root:
  type: FRAME
  fills: fill_bg
  layout:
    dimensions: {width: 400, height: 200}
EL-text:
  type: TEXT
  text: 标题
  textStyle: {fontSize: 18, fontWeight: 500}
  fills: ['#777777']
NODES:
[CANVAS] "Page" #0:1
  [FRAME] "主页面" #1:1 template=EL-root
    [TEXT] #1:2 template=EL-text
'''

class ModelChainTests(unittest.TestCase):
    def test_unknown_geometry_and_defaults_cannot_become_defects(self):
        with tempfile.TemporaryDirectory() as td:
            src, dest = Path(td)/'raw.json', Path(td)/'model.json'
            src.write_text(json.dumps(conv.convert(SAMPLE, '主页面')), encoding='utf-8')
            p = subprocess.run([sys.executable, str(S/'extract_design_fields.py'), '--input', str(src), '--output', str(dest)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            model = json.loads(dest.read_text(encoding='utf-8'))
            self.assertEqual(model.get('meta', {}).get('coordinateBasis'), 'unavailable')
            node = model['flat'][1]
            self.assertIsNone(node['rect']['x'])
            self.assertIsNone(node['rect']['w'])
            self.assertIsNone(node['text']['letterSpacing']['px'])
            item = {'sel': 'h1', 'text': '标题', 'fontSize': '24px', 'fontWeight': '500', 'letterSpacing':'2px', 'opacity':'0.5', 'paddingLeft':'10px', 'rect':{'x':100,'y':30,'w':200,'h':30}}
            diffs = compare.compare_pair(node, item, '', '', '400x200')
            confirmed = [x for x in diffs if x['status']=='confirmed']
            self.assertTrue(any(x['property']=='fontSize' for x in confirmed))
            self.assertFalse(any(x['property'] in ('x','y','w','h','letterSpacing','opacity','paddingLeft') for x in confirmed), confirmed)

    def test_unsupported_backdrop_cannot_fall_through_to_white(self):
        tree = extract.node_model({'id':'r','type':'FRAME','fills':[{'type':'SOLID','color':{'r':1,'g':1,'b':1}}], 'children':[{'id':'c','type':'FRAME','fills':[{'type':'UNSUPPORTED'}], 'pluginData':{'source':'figma-mcp-yaml-ish','uncertainties':['unknown fill']},'children':[{'id':'t','type':'TEXT','characters':'hello','fills':[{'type':'SOLID','color':{'r':0.5,'g':0.5,'b':0.5}}], 'style':{'fontSize':14}}]}]})
        findings = design_contrast.analyse({'tree':tree})['findings']
        self.assertFalse(any(x['status']=='confirmed' for x in findings), findings)
        self.assertTrue(any(x['status']=='pending' for x in findings), findings)

    def test_normalized_claims_reach_report_without_geometry_false_positive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def run(script, *args):
                p = subprocess.run([sys.executable, str(S/script), *map(str,args)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
            raw, model, page, diffs, report, out = [root/n for n in ('raw.json','model.json','page.json','diffs.json','report.json','reports')]
            raw.write_text(json.dumps(conv.convert(SAMPLE, '主页面')), encoding='utf-8')
            page.write_text(json.dumps({'items':[{'selector':'h1','text':'标题','fontSize':'24px','fontWeight':'500','color':'rgb(119,119,119)','rect':{'x':100,'y':30,'w':200,'h':30}}], 'viewport':{'w':400,'h':200}}),encoding='utf-8')
            report.write_text('{}',encoding='utf-8')
            run('extract_design_fields.py','--input',raw,'--output',model)
            run('compare_fields.py','--design',model,'--page',page,'--output',diffs)
            result=json.loads(diffs.read_text(encoding='utf-8'))
            self.assertTrue(any(x['property']=='fontSize' for x in result['defects']),result)
            self.assertFalse(any(x['property'] in ('x','y','w','h','letterSpacing','opacity') for x in result['defects']))
            self.assertTrue(result['parseWarnings']['unavailableDesignProperties'])
            self.assertEqual(result['positionBasis'], 'unavailable')
            run('assemble_report.py','--report',report,'--style-diffs',diffs,'--write')
            run('validate_defects.py','--report',report,'--strict')
            run('generate_reports.py','--input',report,'--outdir',out)
            self.assertTrue((out/'report.html').is_file())
            self.assertTrue((out/'defects.csv').read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_global_style_reference_remains_comparable_after_extraction(self):
        text=SAMPLE.replace("fill_bg: ['#FFFFFF']", "fill_bg: ['#FFFFFF']\nstyle_title: {fontSize: 18, fontWeight: 500}").replace('textStyle: {fontSize: 18, fontWeight: 500}', 'textStyle: style_title')
        raw=conv.convert(text,'主页面')
        node=extract.node_model(raw['document'])['children'][0]
        self.assertEqual(node['text']['characters'],'标题')
        self.assertEqual(node['text']['fontSize'],18)
        self.assertEqual(node['text']['fontWeight'],500)

    def test_unsupported_effect_and_css_radius_are_not_lost(self):
        text=SAMPLE.replace("fills: fill_bg", "fills: fill_bg\n  effects: {boxShadow: '0px 2px 4px #000000'}\n  borderRadius: 8px",1)
        root=conv.convert(text,'主页面')['document']
        self.assertEqual(root.get('cornerRadius'),8)
        self.assertTrue(root['pluginData']['uncertainties'])
        self.assertIsInstance(root['effects'],list)

    def test_yaml_loader_does_not_mutate_global_boolean_resolvers(self):
        import yaml
        conv.convert(SAMPLE,'主页面')
        self.assertIs(yaml.safe_load('true'),True)
        self.assertIs(yaml.safe_load('off'),False)

    def test_design_only_findings_keep_numeric_placeholder_not_fake_coordinates(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            raw=root/'raw.json'; model=root/'model.json'; findings=root/'findings.json'; report=root/'report.json'
            raw.write_text(json.dumps(conv.convert(SAMPLE, '主页面')),encoding='utf-8')
            report.write_text('{}',encoding='utf-8')
            commands=[('extract_design_fields.py','--input',raw,'--output',model),
                      ('design_contrast.py','--design',model,'--out',findings),
                      ('assemble_report.py','--report',report,'--findings',findings,'--write'),
                      ('validate_defects.py','--report',report,'--strict')]
            for script,*args in commands:
                p=subprocess.run([sys.executable,str(S/script),*map(str,args)],capture_output=True,text=True)
                self.assertEqual(p.returncode,0,script+': '+p.stderr)
            data=json.loads(findings.read_text(encoding='utf-8'))
            self.assertTrue(data['findings'])
            self.assertEqual(data['findings'][0]['element']['bbox']['x'],0)
            self.assertEqual(data['findings'][0]['raw']['coordinateBasis'],'unavailable')

    def test_multiple_frames_require_explicit_selection(self):
        text=SAMPLE+'  [FRAME] "另一个" #2:1 template=EL-root\n'
        with self.assertRaises(ValueError):
            conv.convert(text)
        self.assertEqual(conv.convert(text,node_id='2:1')['document']['id'],'2:1')

    def test_missing_globals_and_malformed_nodes_fail_closed(self):
        with self.assertRaises(ValueError):
            conv.convert(SAMPLE.replace('fills: fill_bg','fills: fill_absent'),'主页面')
        with self.assertRaises(ValueError):
            conv.convert(SAMPLE+'    broken node line\n','主页面')
        with self.assertRaises(ValueError):
            conv.convert(SAMPLE+'    [TEXT] #1:2 text="dup"\n','主页面')

    def test_transparent_color_is_not_multiplied_twice(self):
        text=SAMPLE.replace("['#777777']", "['rgba(119,119,119,0.5)']")
        node=extract.node_model(conv.convert(text,'主页面')['document'])['children'][0]
        paint=node['fills'][0]
        effective=design_contrast.paint_color(paint)
        self.assertEqual(effective['a'],0.5)

    def test_unsupported_fill_keeps_pending_backdrop(self):
        text=SAMPLE.replace("['#777777']", "['linear-gradient(red, blue)']")
        doc=conv.convert(text,'主页面')['document']
        self.assertFalse(doc['children'][0]['pluginData']['propertyAvailability']['fills'])
        self.assertTrue(doc['children'][0]['pluginData']['uncertainties'])
        result=design_contrast.analyse({'tree':extract.node_model(doc)})
        self.assertTrue(any(x['status']=='pending' for x in result['findings']))
        self.assertFalse(any(x['status']=='confirmed' for x in result['findings']))

    def test_font_style_inferred_weight_remains_comparable(self):
        text = SAMPLE.replace('fontWeight: 500', 'fontStyle: Medium')
        node = extract.node_model(conv.convert(text, '主页面')['document'])['children'][0]
        self.assertEqual(node['text']['fontWeight'], 500)
        diffs = compare.compare_pair(node, {'text': '标题', 'fontWeight': '900'}, '', '', '400')
        self.assertTrue(any(d['property'] == 'fontWeight' and d['status'] == 'confirmed' for d in diffs))

    def test_px_font_size_is_normalized_before_extraction(self):
        text = SAMPLE.replace('fontSize: 18', "fontSize: '18px'")
        doc = conv.convert(text, '主页面')['document']
        self.assertEqual(doc['children'][0]['style']['fontSize'], 18)
        self.assertEqual(extract.node_model(doc)['children'][0]['text']['fontSize'], 18)

    def test_unsupported_units_remain_unknown_through_extraction(self):
        text = SAMPLE.replace('fills: fill_bg', "fills: fill_bg\n  borderRadius: '50%'\n  layout: {padding: '10%', gap: '2em'}", 1)
        doc = conv.convert(text, '主页面')['document']
        available = doc['pluginData']['propertyAvailability']
        self.assertFalse(available['radius'])
        self.assertFalse(available['layout.padding'])
        self.assertFalse(available['layout.itemSpacing'])
        model = extract.node_model(doc)
        self.assertIsNone(model['radius'])
        self.assertIsNone(model['layout']['padding']['top'])
        self.assertIsNone(model['layout']['itemSpacing'])
        self.assertTrue(model['sourceEvidence']['uncertainties'])

    def test_unsupported_font_units_remain_unknown_without_crash(self):
        text = SAMPLE.replace('fontSize: 18', "fontSize: '1.2rem'")
        doc = conv.convert(text, '主页面')['document']
        node = extract.node_model(doc)['children'][0]
        self.assertIsNone(node['text']['fontSize'])
        self.assertFalse(node['sourceEvidence']['propertyAvailability']['text.fontSize'])

    def test_raw_paint_color_alpha_and_opacity_are_multiplied_once(self):
        text = SAMPLE.replace("['#777777']", "[{type: SOLID, color: {r: 0.5, g: 0.5, b: 0.5, a: 0.5}, opacity: 0.5}]")
        node = extract.node_model(conv.convert(text, '主页面')['document'])['children'][0]
        self.assertEqual(design_contrast.paint_color(node['fills'][0])['a'], 0.25)
        self.assertEqual(compare.design_color(node)['alpha'], 0.25)

    def test_translucent_fill_comparison_is_skipped_with_evidence(self):
        text = SAMPLE.replace("['#777777']", "['rgba(119,119,119,0.5)']")
        node = extract.node_model(conv.convert(text, '主页面')['document'])['children'][0]
        skips = []
        diffs = compare.compare_pair(node, {'text': '标题', 'color': '#000000'}, '', '', '400', color_skips=skips)
        self.assertFalse(any(d['property'] == 'color' for d in diffs))
        self.assertTrue(any(s['kind'] == 'low-alpha-fill' for s in skips))

    def test_uncertain_passing_contrast_remains_pending(self):
        text = SAMPLE.replace('  fills: fill_bg\n', '', 1).replace("['#777777']", "['#000000']")
        result = design_contrast.analyse({'tree': extract.node_model(conv.convert(text, '主页面')['document'])})
        self.assertEqual(result['status'], 'pending')
        self.assertTrue(any(f['status'] == 'pending' for f in result['findings']))
        self.assertFalse(any(f['status'] == 'pass' for f in result['findings']))

    def test_unknown_ancestor_opacity_prevents_contrast_pass(self):
        text = SAMPLE.replace("['#777777']", "['#000000']")
        result = design_contrast.analyse({'tree': extract.node_model(conv.convert(text, '主页面')['document'])})
        self.assertEqual(result['status'], 'pending')

    def test_hidden_and_zero_opacity_root_skip_contrast(self):
        for attrs in ('visible=false', 'opacity=0'):
            with self.subTest(attrs=attrs):
                text = SAMPLE.replace('template=EL-root', 'template=EL-root ' + attrs)
                tree = extract.node_model(conv.convert(text, '主页面')['document'])
                result = design_contrast.analyse({'tree': tree})
                self.assertEqual(result['status'], 'notRun')
                self.assertEqual(result['summary']['textChecked'], 0)

    def test_unknown_text_spacing_units_cannot_become_zero_spacing(self):
        text = SAMPLE.replace('fontWeight: 500', "fontWeight: 500, letterSpacing: '2rem', lineHeight: '2rem'")
        node = extract.node_model(conv.convert(text, '主页面')['document'])['children'][0]
        self.assertIsNone(node['text']['letterSpacing']['px'])
        self.assertIsNone(node['text']['lineHeight']['px'])
        self.assertFalse(node['sourceEvidence']['propertyAvailability']['text.letterSpacing'])
        self.assertFalse(node['sourceEvidence']['propertyAvailability']['text.lineHeight'])

    def test_unknown_opacity_does_not_crash_extraction(self):
        text = SAMPLE.replace('template=EL-root', 'template=EL-root opacity=unknown')
        model = extract.node_model(conv.convert(text, '主页面')['document'])
        self.assertIsNone(model['opacity'])

    def test_hidden_mcp_ancestors_exclude_descendants_from_matching(self):
        for attrs in ('visible=false', 'opacity=0'):
            with self.subTest(attrs=attrs):
                text = SAMPLE.replace('template=EL-root', 'template=EL-root ' + attrs)
                tree = extract.node_model(conv.convert(text, '主页面')['document'])
                visible = [n for n in extract.flatten([tree]) if n.get('visible', True)]
                self.assertEqual(visible, [])

    def test_zero_opacity_paint_is_not_treated_as_opaque(self):
        text = SAMPLE.replace("['#777777']", "[{type: SOLID, color: {r: 0.5, g: 0.5, b: 0.5}, opacity: 0}]")
        node = extract.node_model(conv.convert(text, '主页面')['document'])['children'][0]
        self.assertEqual(design_contrast.paint_color(node['fills'][0])['a'], 0)
        self.assertIsNone(compare.design_color(node))

    def test_yaml_hidden_paints_and_effects_remain_hidden(self):
        text = SAMPLE.replace("['#777777']", "[{type: SOLID, color: {r: 1, g: 0, b: 0}, visible: false}]")
        text = text.replace('  text: 标题', '  effects: [{type: DROP_SHADOW, visible: false, radius: 4}]\n  text: 标题')
        node = extract.node_model(conv.convert(text, '主页面')['document'])['children'][0]
        self.assertEqual(node['fills'], [])
        self.assertEqual(node['effects'], [])

    def test_unknown_unitless_text_metrics_do_not_assert_defaults(self):
        text = SAMPLE.replace('fontWeight: 500', "fontWeight: 500, letterSpacing: 2, lineHeight: auto")
        node = extract.node_model(conv.convert(text, '主页面')['document'])['children'][0]
        self.assertEqual(node['text']['letterSpacing']['px'], 2)
        self.assertEqual(node['text']['lineHeight']['unit'], 'AUTO')

    def test_relative_letter_spacing_without_font_size_is_unknown(self):
        text = SAMPLE.replace('fontSize: 18, fontWeight: 500', "fontWeight: 500, letterSpacing: '10%'")
        node = extract.node_model(conv.convert(text, '主页面')['document'])['children'][0]
        self.assertIsNone(node['text']['letterSpacing']['px'])
        diffs = compare.compare_pair(node, {'text': '标题', 'letterSpacing': '4px'}, '', '', '400')
        self.assertFalse(any(d['property'] == 'letterSpacing' for d in diffs))

    def test_font_name_and_explicit_text_fields_survive_availability(self):
        text = SAMPLE.replace('fontWeight: 500', 'fontName: {family: Inter, style: Medium}, fontPostScriptName: Inter-Medium, textAlignVertical: CENTER, paragraphSpacing: 12')
        node = extract.node_model(conv.convert(text, '主页面')['document'])['children'][0]
        self.assertEqual(node['text']['fontWeight'], 500)
        self.assertEqual(node['text']['fontFamily'], 'Inter')
        self.assertEqual(node['text']['fontPostScriptName'], 'Inter-Medium')
        self.assertEqual(node['text']['textAlignVertical'], 'CENTER')
        self.assertEqual(node['text']['paragraphSpacing'], 12)
        diffs = compare.compare_pair(node, {'text': '标题', 'fontWeight': '900'}, '', '', '400')
        self.assertTrue(any(d['property'] == 'fontWeight' and d['status'] == 'confirmed' for d in diffs))

    def test_node_stroke_width_and_alignment_reach_comparison(self):
        text = SAMPLE.replace('  type: FRAME', "  type: FRAME\n  strokeWeight: 8\n  strokeAlign: INSIDE\n  strokes: ['#000000']", 1)
        node = extract.node_model(conv.convert(text, '主页面')['document'])
        self.assertEqual(node['strokes'][0]['cssBorderPx'], 8)
        self.assertEqual(node['strokes'][0]['align'], 'INSIDE')
        diffs = compare.compare_pair(node, {'border': '8px solid rgb(0,0,0)'}, '', '', '400')
        self.assertFalse(any(d['property'] == 'borderWidth' and d['status'] == 'confirmed' for d in diffs), diffs)

    def test_explicit_missing_root_id_never_falls_back_to_first_node(self):
        payload = {'nodes': {'1:1': {'document': {'id': '1:1', 'type': 'FRAME'}}}}
        self.assertIsNone(extract.pick_root(payload, 'missing'))
        self.assertIsNone(extract.pick_root({'document': payload['nodes']['1:1']['document']}, 'missing'))
        self.assertEqual(extract.pick_root(payload, None)['id'], '1:1')
        self.assertEqual(extract.pick_root(payload, '1:1')['id'], '1:1')

    def test_attribute_text_is_not_split_inside_quotes(self):
        tree=conv.parse_nodes('[TEXT] #1:2 text="a layout=x #123" textStyle={"fontSize":18}')
        self.assertEqual(tree[0]['text'], 'a layout=x #123')

    def test_missing_template_and_root_are_rejected(self):
        with self.assertRaises(ValueError):
            conv.convert(SAMPLE.replace('template=EL-text', 'template=EL-absent'), '主页面')
        with self.assertRaises(ValueError):
            conv.convert(SAMPLE, '不存在')

if __name__ == '__main__':
    unittest.main()
