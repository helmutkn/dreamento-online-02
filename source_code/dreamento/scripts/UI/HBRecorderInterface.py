import os

import json
import sys

import numpy as np
import requests

from scripts.ServerConnection.Recorder import Recorder
from scripts.ServerConnection.ZmaxHeadband import ZmaxHeadband
from scripts.SleepScoring import realTimeAutoScoring
from scripts.SleepScoring.SleePyCoInference import SleePyCoInference
from scripts.UI.EEGPlotWindow import EEGVisThread
from scripts.UI.SleepStatePlot import SleepStateThread

from PyQt5.QtCore import QObject



class HBRecorderInterface(QObject):
    def __init__(self):
        super(HBRecorderInterface, self).__init__()

        self.sample_rate = 256
        # signal type
        self.signalType = [0, 1, 2, 3, 4, 5, 7, 8]
        # [
        #   0=eegr, 1=eegl, 2=dx, 3=dy, 4=dz, 5=bodytemp,
        #   6=bat, 7=noise, 8=light, 9=nasal_l, 10=nasal_r,
        #   11=oxy_ir_ac, 12=oxy_r_ac, 13=oxy_dark_ac,
        #   14=oxy_ir_dc, 15=oxy_r_dc, 16=oxy_dark_dc
        # ]

        self.hb = None
        self.recorder = Recorder(self.signalType)

        self.isRecording = False
        self.firstRecording = True

        # stimulations
        self.stimulationDataBase = {}  # have info of all triggered stimulations

        # scoring
        self.sleepScoringConfigPath = 'scripts/SleepScoring/SleePyCo/SleePyCo/configs/SleePyCo-Transformer_SL-10_numScales-3_Sleep-EDF-2018_freezefinetune.json'
        with open(self.sleepScoringConfigPath, 'r') as config_file:
            config = json.load(config_file)
        config['name'] = os.path.basename(self.sleepScoringConfigPath).replace('.json', '')
        self.sleepScoringConfig = config

        self.inferenceModel = None
        self.sleepScoringModel = None
        self.scoring_predictions = []
        self.epochCounter = 0
        self.sleepScoringModelPath = None

        # visualization
        self.eegThread = EEGVisThread()
        self.sleepStateThread = SleepStateThread()

        # program parameters
        self.scoreSleep = False

        # webhook
        self.webHookBaseAdress = "http://127.0.0.1:5000/"
        self.webhookActive = False

    def connect_to_software(self):
        self.hb = ZmaxHeadband()
        if self.hb.readSocket is None or self.hb.writeSocket is None:  # HDServer is not running
            print('Sockets can not be initialized.')
        else:
            print('Connected')

    def start_recording(self):
        if self.isRecording:
            return

        self.recorder = Recorder(self.signalType)

        if self.firstRecording:
            # TODO: init sleep scoring model here

            self.firstRecording = False

        self.isRecording = True

        self.recorder.start()

        self.recorder.recorderThread.finished.connect(self.on_recording_finished)
        self.recorder.recorderThread.recordingFinishedSignal.connect(self.on_recording_finished_write_stimulation_db)
        self.recorder.recorderThread.sendEEGdata2MainWindow.connect(self.getEEG_from_thread)  # sending data for plotting, scoring, etc.

        print('recording started')

    def stop_recording(self):
        if not self.isRecording:
            return

        self.recorder.stop()
        self.isRecording = False
        print('recording stopped')

    def on_recording_finished(self):
        # when the recording is finished, this function is called
        self.isRecording = False
        print('recording finished')

    def on_recording_finished_write_stimulation_db(self, fileName):
        # save triggered stimulation information on disk:
        with open(f'{fileName}-markers.json', 'w') as fp:
            json.dump(self.stimulationDataBase, fp, indent=4, separators=(',', ': '))

        with open(f"{fileName}-predictions.txt", "a") as outfile:
            if self.scoring_predictions:
                # stagesList = ['W', 'N1', 'N2', 'N3', 'REM', 'MOVE', 'UNK']
                self.scoring_predictions.insert(0, -1)  # first epoch is not predicted, therefore put -1 instead
                outfile.write("\n".join(str(item) for item in self.scoring_predictions))

    def set_sleep_scoring_model(self, path):
        self.sleepScoringModelPath = path

    def start_scoring(self):
        self.scoreSleep = True

    def stop_scoring(self):
        self.scoreSleep = False

    def getEEG_from_thread(self, eegSignal_r, eegSignal_l, epoch_counter=0):
        print(epoch_counter)
        self.epochCounter = epoch_counter

        if self.eegThread.is_alive():
            sigR = eegSignal_r
            sigL = eegSignal_l
            t = [number / self.sample_rate for number in range(len(eegSignal_r))]
            self.eegThread.update_plot(t, sigR, sigL)

        predictionToTransmit = None
        if self.scoreSleep:
            if self.inferenceModel is None:
                self.inferenceModel = SleePyCoInference(1, self.sleepScoringConfig)
                print('sleep scoring model imported')

            #if self.sleepScoringModel is None:
            #    self.sleepScoringModel = realTimeAutoScoring.importModel(self.sleepScoringModelPath)
            #    print('sleep scoring model imported')

            # 30 seconds, each 256 samples... send recording for last 30 seconds to model for prediction
            #sigRef = np.asarray(eegSignal_r)
            #sigReq = np.asarray(eegSignal_l)
            #sigRef = sigRef.reshape((1, sigRef.shape[0]))
            #sigReq = sigReq.reshape((1, sigReq.shape[0]))

            # inference
            modelPrediction = self.inferenceModel.infere(np.asarray(eegSignal_r).reshape(1,1,len(eegSignal_r)))
            #modelPrediction = realTimeAutoScoring.Predict_array(
            #    output_dir="./DataiBand/output/Fp1-Fp2_filtered",
            #    args_log_file="info_ch_extract.log", filtering_status=True,
            #    lowcut=0.3, highcut=30, fs=256, signal_req=sigReq, signal_ref=sigRef, model=self.sleepScoringModel)


            predictionToTransmit = int(modelPrediction[0])
            # self.displayEpochPredictionResult(int(modelPrediction[0]),
            #                                  int(self.epochCounter))  # display prediction result on mainWindow
            self.scoring_predictions.append(int(modelPrediction[0]))
            self.sleepStateThread.update_text(str(modelPrediction[0]))

        if self.webhookActive:
            if self.scoreSleep:
                data = {'state': predictionToTransmit}
                requests.post(self.webHookBaseAdress + 'sleepstate', data=data)

    def show_eeg_signal(self):
        if self.eegThread.is_alive():
            pass
        else:
            self.eegThread = EEGVisThread()
            self.eegThread.start()
            #self.eegPlot = EEGPlotWindow(self.sample_rate)
            #self.eegPlot.show()

    def start_webhook(self):
        self.webhookActive = True

    def stop_webhook(self):
        self.webhookActive = False

    def set_signaltype(self, types: list = []):
        self.signalType = types

    def quit(self):
        self.recorder.stop()
        self.eegThread.stop()
