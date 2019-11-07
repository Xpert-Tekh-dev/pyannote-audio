#!/usr/bin/env python
# encoding: utf-8

# The MIT License (MIT)

# Copyright (c) 2017-2019 CNRS

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# AUTHORS
# Hervé BREDIN - http://herve.niderb.fr

from pathlib import Path

from pyannote.core import Annotation
from pyannote.database import get_annotated

from pyannote.metrics.diarization import GreedyDiarizationErrorRate

from .speech_turn_segmentation import SpeechTurnSegmentation
from .speech_turn_clustering import SpeechTurnClustering
from .speech_turn_assignment import SpeechTurnClosestAssignment

from typing import Optional
from typing import Union
from pyannote.pipeline import Pipeline
from pyannote.pipeline.parameter import Uniform


class SpeakerDiarization(Pipeline):
    """Speaker diarization pipeline

    Parameters
    ----------
    sad_scores : `Path` or 'oracle'
        Path to precomputed speech activity detection scores.
        Use 'oracle' to assume perfect speech activity detection.
    scd_scores : `Path`
        Path to precomputed SCD scores on disk
    embedding : `Path`
        Path to precomputed embedding on disk
    metric : {'euclidean', 'cosine', 'angular'}, optional
        Metric used for comparing embeddings. Defaults to 'cosine'.
    method : {'pool', 'affinity_propagation'}
        Clustering method. Defaults to 'pool'.
    evaluation_only : `bool`
        Only process the evaluated regions. Default to False.

    Hyper-parameters
    ----------------
    min_duration : `float`
        Do not cluster speech turns shorter than `min_duration`. Assign them to
        the closest cluster (of long speech turns) instead.
    """

    def __init__(self, sad_scores: Optional[Union[Path, str]] = None,
                       scd_scores: Optional[Path] = None,
                       embedding: Optional[Path] = None,
                       metric: Optional[str] = 'cosine',
                       method: Optional[str] = 'pool',
                       evaluation_only: Optional[bool] = False):

        super().__init__()

        self.sad_scores = sad_scores
        self.scd_scores = scd_scores
        self.speech_turn_segmentation = SpeechTurnSegmentation(
            sad_scores=self.sad_scores,
            scd_scores=self.scd_scores)
        self.evaluation_only = evaluation_only

        self.min_duration = Uniform(0, 10)

        self.embedding = embedding
        self.metric = metric
        self.method = method
        self.speech_turn_clustering = SpeechTurnClustering(
            embedding=self.embedding, metric=self.metric, method=self.method)

        self.speech_turn_assignment = SpeechTurnClosestAssignment(
            embedding=self.embedding, metric=self.metric)

    def __call__(self, current_file: dict) -> Annotation:
        """Apply speaker diarization

        Parameters
        ----------
        current_file : `dict`
            File as provided by a pyannote.database protocol.

        Returns
        -------
        hypothesis : `pyannote.core.Annotation`
            Speaker diarization output.
        """

        # segmentation into speech turns
        speech_turns = self.speech_turn_segmentation(current_file)

        # some files are only partially annotated and therefore one cannot
        # evaluate speaker diarization results on the whole file.
        # this option simply avoids trying to cluster those
        # (potentially messy) un-annotated refions by focusing only on
        # speech turns contained in the annotated regions.
        if self.evaluation_only:
            annotated = get_annotated(current_file)
            speech_turns = speech_turns.crop(annotated, mode='intersection')

        # in case there is one speech turn or less, there is no need to apply
        # any kind of clustering approach.
        if len(speech_turns) < 2:
            return speech_turns

        # split short/long speech turns. the idea is to first cluster long
        # speech turns (i.e. those for which we can trust embeddings) and then
        # assign each speech turn to the closest cluster.
        long_speech_turns = speech_turns.empty()
        shrt_speech_turns = speech_turns.empty()
        for segment, track, label in speech_turns.itertracks(yield_label=True):
            if segment.duration < self.min_duration:
                shrt_speech_turns[segment, track] = label
            else:
                long_speech_turns[segment, track] = label

        # in case there are no long speech turn to cluster, we return the
        # original speech turns (= shrt_speech_turns)
        if len(long_speech_turns) < 1:
            return speech_turns

        # first: cluster long speech turns
        long_speech_turns = self.speech_turn_clustering(current_file,
                                                        long_speech_turns)

        # then: assign short speech turns to clusters
        long_speech_turns.rename_labels(generator='string', copy=False)

        if len(shrt_speech_turns) > 0:
            shrt_speech_turns.rename_labels(generator='int', copy=False)
            shrt_speech_turns = self.speech_turn_assignment(current_file,
                                                            shrt_speech_turns,
                                                            long_speech_turns)
        # merge short/long speech turns
        return long_speech_turns.update(
            shrt_speech_turns, copy=False).support(collar=0.)

        # TODO. add GMM-based resegmentation

    def get_metric(self) -> GreedyDiarizationErrorRate:
        """Return new instance of diarization error rate metric"""
        return  GreedyDiarizationErrorRate(collar=0.0, skip_overlap=False)


class Yin2018(SpeakerDiarization):
    """Speaker diarization pipeline introduced in Yin et al., 2018

    Ruiqing Yin, Hervé Bredin, and Claude Barras. "Neural speech turn
    segmentation and affinity propagation for speaker diarization".
    Interspeech 2018.

    Parameters
    ----------
    sad_scores : `Path`
        Path to precomputed speech activity detection scores.
    scd_scores : `Path`
        Path to precomputed speaker change detection scores
    embedding : `Path
        Path to precomputed embeddings.
    metric : {'euclidean', 'cosine', 'angular'}, optional
        Metric used for comparing embeddings. Defaults to 'cosine'.
    evaluation_only : `bool`
        Only process the evaluated regions. Default to False.
    """
    def __init__(self, sad_scores: Optional[Path] = None,
                       scd_scores: Optional[Path] = None,
                       embedding: Optional[Path] = None,
                       metric: Optional[str] = 'cosine',
                       evaluation_only: Optional[bool] = False):

        super().__init__(sad_scores=sad_scores,
                         scd_scores=scd_scores,
                         embedding=embedding,
                         metric=metric,
                         method='affinity_propagation',
                         evaluation_only=evaluation_only)

        self.freeze({
            'min_duration': 0.,
            'speech_turn_segmentation': {
                'speech_activity_detection': {'min_duration_on': 0.,
                                              'min_duration_off': 0.,
                                              'pad_onset': 0.,
                                              'pad_offset': 0.}}})
