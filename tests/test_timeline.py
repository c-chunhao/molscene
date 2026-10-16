"""Independent analytic cameras and actual PyMOL display/state transitions."""
import copy
import importlib.util
import math
import unittest

try:
    from molscene import timeline
except ImportError:
    timeline = None


def view(angle=0):
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [c, 0., s, 0., 1., 0., -s, 0., c, 0., 0., -20., 0., 0., 0., 10., 30., 1.]


def row(frame, angle=0, scene=None, state=1, interpolation='smooth'):
    return dict(id='k'+str(frame), label='', frame=frame, scene=scene, state=state,
                view=view(angle), interpolation=interpolation)


def document(*rows):
    rows = list(rows) or [row(1), row(5, 90)]
    return dict(schema_version=1, baseline_scene='base', fps=24,
                frame_count=rows[-1]['frame'], keyframes=rows)


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(timeline, 'timeline implementation is required')

    def test_analytic_slerp_exact_endpoints_shortest_arc_and_hold(self):
        doc = document()
        self.assertEqual(timeline.sample_timeline(doc, 1)['view'], view())
        self.assertEqual(timeline.sample_timeline(doc, 5)['view'], view(90))
        for got, want in zip(timeline.sample_timeline(doc, 3)['view'], view(45)):
            self.assertAlmostEqual(got, want, places=12)
        short = timeline.sample_timeline(document(row(1,170), row(5,-170)), 3)
        for got, want in zip(short['view'], view(180)):
            self.assertAlmostEqual(got, want, places=12)
        for angle in (1e-10, 180):
            value = timeline.sample_timeline(document(row(1), row(5,angle)), 3)
            self.assertTrue(all(math.isfinite(x) for x in value['view']))
            self.assertEqual(value, timeline.sample_timeline(document(row(1), row(5,angle)), 3))
        held = timeline.sample_timeline(document(row(1,interpolation='hold'),row(5,90)),3)
        self.assertEqual(held['view'], view())
        self.assertEqual(held['time_seconds'], 2/24)

    def test_scene_and_state_cut_only_at_keyframe(self):
        doc = document(row(1), row(3, scene='red',state=2),row(5,state=1))
        self.assertEqual([timeline.sample_timeline(doc,f)['scene'] for f in range(1,6)],
                         ['base','base','red','red','red'])
        self.assertEqual([timeline.sample_timeline(doc,f)['state'] for f in range(1,6)], [1,1,2,2,1])

    def test_rejects_invalid_documents_without_mutating_input(self):
        invalid = []
        for key, value in [('fps',True),('fps',0),('fps',121),('schema_version',True),
                           ('frame_count',4),('baseline_scene',''),('extra',1),('keyframes',[])]:
            d=document(); d[key]=value; invalid.append(d)
        for key, value in [('id','!'),('label','a'*257),('frame',True),('frame',0),
                           ('state',0),('state',100001),('scene','a\0b'),('interpolation','linear'),
                           ('view',view()[:-1]),('extra',1)]:
            d=document(); d['keyframes'][0][key]=value; invalid.append(d)
        for index, value in [(0,float('nan')),(0,2),(15,40),(17,0),(0,10**500)]:
            d=document(); d['keyframes'][0]['view'][index]=value; invalid.append(d)
        d=document(); d['keyframes'][1]['frame']=1; invalid.append(d)
        d=document(); d['keyframes'][1]['id']='k1'; invalid.append(d)
        d=document(); del d['fps']; invalid.append(d)
        for d in invalid:
            with self.subTest(document=d), self.assertRaises(ValueError):
                timeline.validate_timeline(d)
        d=document(); original=copy.deepcopy(d)
        normalized=timeline.validate_timeline(d); normalized['keyframes'][0]['view'][0]=9
        self.assertEqual(d,original)
        for f in (0,6,True,1.5):
            with self.assertRaises(ValueError): timeline.sample_timeline(d,f)

    def test_hold_allows_projection_cut_and_scene_reference_rename(self):
        d=document(row(1,interpolation='hold'),row(5,scene='display'))
        d['keyframes'][1]['view'][17]=-20
        self.assertEqual(timeline.sample_timeline(d,4)['view'][17],1)
        self.assertEqual(timeline.sample_timeline(d,5)['view'][17],-20)
        renamed=timeline.rename_scene_reference(d,'display','new display')
        self.assertTrue(timeline.scene_is_referenced(renamed,'new display'))
        self.assertFalse(timeline.scene_is_referenced(renamed,'display'))

    def test_finite_camera_translation_does_not_overflow_interpolation(self):
        d=document()
        d['keyframes'][0]['view'][9]=1e308
        d['keyframes'][1]['view'][9]=-1e308
        result=timeline.sample_timeline(d,3)
        self.assertEqual(result['view'][9],0.)
        self.assertTrue(all(math.isfinite(value) for value in result['view']))

    def test_edits_recompute_duration_and_leave_original_untouched(self):
        doc=document(); original=copy.deepcopy(doc)
        added=timeline.edit_keyframe(doc,'add',values=row(9))
        self.assertEqual(added['frame_count'],9)
        self.assertEqual(doc,original)
        moved=timeline.edit_keyframe(added,'move_up','k9')
        self.assertEqual([r['id'] for r in moved['keyframes']],['k1','k9','k5'])
        removed=timeline.edit_keyframe(added,'remove','k1')
        self.assertEqual([r['frame'] for r in removed['keyframes']],[1,9])
        removed=timeline.edit_keyframe(added,'remove','k9')
        self.assertEqual(removed['frame_count'],5)
        self.assertEqual(timeline.edit_keyframe(doc,'set_fps',values={'fps':30})['fps'],30)
        with self.assertRaises(ValueError): timeline.edit_keyframe(doc,'update','k5',{'frame':1})
        with self.assertRaises(ValueError): timeline.edit_keyframe(document(row(1)),'remove','k1')
        with self.assertRaises(ValueError): timeline.edit_keyframe(doc,'update','k5',{'id':'changed'})
        renamed=timeline.rename_scene_reference(doc,'base','new')
        self.assertTrue(timeline.scene_is_referenced(renamed,'new'))
        self.assertFalse(timeline.scene_is_referenced(renamed,'base'))


@unittest.skipUnless(importlib.util.find_spec('pymol2'), 'PyMOL unavailable')
class TimelineNativeTests(unittest.TestCase):
    def test_random_seek_display_states_movie_and_fingerprints(self):
        self.assertIsNotNone(timeline, 'timeline implementation is required')
        import pymol2
        from molscene.snapshot import scene_fingerprint
        with pymol2.PyMOL() as pm:
            cmd=pm.cmd
            cmd.pseudoatom('mol',pos=[0,0,0],state=1)
            cmd.pseudoatom('mol',pos=[3,0,0],state=2)
            cmd.pseudoatom('other',pos=[0,2,0])
            cmd.color('blue','mol'); cmd.hide('everything'); cmd.show('spheres','mol')
            cmd.set('state',2)
            doc=timeline.new_timeline(cmd)
            self.assertEqual(doc['keyframes'][0]['state'],2)
            cmd.color('red','mol'); cmd.hide('spheres'); cmd.show('sticks','mol'); cmd.disable('other')
            special="red ' ; print('DATA')"
            cmd.scene(special,'store')
            doc=timeline.edit_keyframe(doc,'add',values=timeline.capture_keyframe(cmd,frame=3,state=1,scene=special))
            doc=timeline.edit_keyframe(doc,'add',values=timeline.capture_keyframe(cmd,frame=5,state=2))
            cmd.mset('1 x8'); movie=copy.deepcopy(cmd.get_session()['movie'])
            before={s:scene_fingerprint(cmd,s) for s in (1,2)}
            for f,color,enabled,state in [(5,'red',False,2),(1,'blue',True,2),(4,'red',False,1),(2,'blue',True,2)]:
                cmd.mplay()
                self.assertTrue(cmd.get_movie_playing())
                timeline.seek_timeline(cmd,doc,f)
                self.assertFalse(cmd.get_movie_playing())
                display=[]; cmd.iterate('mol','display.append((color, reps))',space={'display':display})
                self.assertEqual(display[0][0],cmd.get_color_index(color))
                self.assertEqual('other' in cmd.get_names('objects',enabled_only=1),enabled)
                self.assertEqual(cmd.get_state(),state)
                reps=display[0][1]
                self.assertEqual(bool(reps & (1<<1)),color=='blue')
            self.assertEqual(cmd.get_session()['movie'],movie)
            self.assertEqual(before,{s:scene_fingerprint(cmd,s) for s in (1,2)})
            cmd.scene(special,'delete'); old_view=cmd.get_view(); old_state=cmd.get_state()
            with self.assertRaises(ValueError): timeline.seek_timeline(cmd,doc,3)
            self.assertEqual(cmd.get_view(),old_view); self.assertEqual(cmd.get_state(),old_state)


if __name__ == '__main__': unittest.main()
