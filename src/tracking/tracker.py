"""BoT-SORT association only; no learned identity or lost-box export."""
from types import SimpleNamespace


class PersonTracker:
    def __init__(self,config,fps):
        import numpy as np
        from ultralytics.trackers.bot_sort import BOTSORT,BOTrack
        from ultralytics.trackers.byte_tracker import BYTETracker,STrack
        # Explicit settings: no auto ReID model downloads, fixed-camera classroom.
        self.settings={'tracker_type':config.tracker,'track_high_thresh':.25,'track_low_thresh':.1,
            'new_track_thresh':.25,'track_buffer':round(30*config.buffer_seconds),'match_thresh':.8,
            'fuse_score':True,'gmc_method':'none','proximity_thresh':.5,'appearance_thresh':.25,'with_reid':False,'model':'auto'}
        parent=BOTSORT if config.tracker=='botsort' else BYTETracker
        track_cls=BOTrack if config.tracker=='botsort' else STrack
        class SourceIndexedTracker(parent):
            def init_track(self,results,img=None):
                if not len(results):return []
                # v8.3.251's default init_track re-numbers high/low subsets.
                # Carry original detector indices through BOTH association passes.
                xywh=np.concatenate([results.xywh,results.source_indices.reshape(-1,1)],axis=-1)
                return [track_cls(box,score,label) for box,score,label in zip(xywh,results.conf,results.cls)]
        self.engine=SourceIndexedTracker(SimpleNamespace(**self.settings),frame_rate=fps/config.stride)

    def update(self,boxes,image,detections):
        rows=self.engine.update(IndexedDetections(boxes),image);matches=[]
        for row in rows:
            if len(row)<8:raise ValueError('unexpected_tracker_output_contract')
            tid=int(row[4]);idx=int(row[7])
            if tid<1 or idx<0 or idx>=len(detections) or int(row[6])!=0:raise ValueError('tracker_detection_binding_invalid')
            if abs(float(row[5])-detections[idx]['confidence'])>1e-5:raise ValueError('tracker_score_not_bound_to_current_detection')
            matches.append({'track_id':tid,'detection_index':idx})
        return matches


class IndexedDetections:
    def __init__(self,boxes,indices=None):
        import numpy as np
        self.xywh=boxes.xywh;self.xyxy=boxes.xyxy;self.conf=boxes.conf;self.cls=boxes.cls
        self.source_indices=np.arange(len(boxes)) if indices is None else indices
    def __len__(self):return len(self.conf)
    def __getitem__(self,index):
        obj=object.__new__(IndexedDetections)
        for name in ['xywh','xyxy','conf','cls','source_indices']:setattr(obj,name,getattr(self,name)[index])
        return obj
