import argparse
import asyncio
from typing import List

import cv2
import numpy as np
from client import PeopleDetectorClient
from farm_ng.oak import oak_pb2
from farm_ng.oak.camera_client import OakCameraClient
from farm_ng.people_detection import people_detection_pb2
from farm_ng.service.service_client import ClientConfig
from limbus.core import Component
from limbus.core import ComponentState
from limbus.core import InputParams
from limbus.core import OutputParams
from limbus.core.pipeline import Pipeline


class AmigaCamera(Component):
    def __init__(self, name: str, config: ClientConfig, stream_every_n: int) -> None:
        super().__init__(name)
        # configure the camera client
        self.client = OakCameraClient(config)
        # create a stream
        self.stream = self.client.stream_frames(every_n=stream_every_n)

    @staticmethod
    def register_outputs(outputs: OutputParams) -> None:
        outputs.declare("image", np.ndarray)

    def _decode_image(self, image_data: bytes) -> np.ndarray:
        image: np.ndarray = np.frombuffer(image_data, dtype="uint8")
        image = cv2.imdecode(image, cv2.IMREAD_UNCHANGED)
        scale_percent =60
        width = int(image.shape[1]*scale_percent/100)
        height = int(image.shape[0]*scale_percent/100)
        dim = (width,height)

        image = cv2.resize(image,dim)
        return image

    async def forward(self) -> ComponentState:
        response = await self.stream.read()
        frame: oak_pb2.OakSyncFrame = response.frame

        await self.outputs.image.send(self._decode_image(frame.rgb.image_data))

        return ComponentState.OK


class OpenCvCamera(Component):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        # configure the camera client
        self.grabber = cv2.VideoCapture(0)

    @staticmethod
    def register_outputs(outputs: OutputParams) -> None:
        outputs.declare("image", np.ndarray)

    async def forward(self) -> ComponentState:
        ret, frame = self.grabber.read()
        if not ret:
            return ComponentState.STOPPED

        await self.outputs.image.send(frame)

        return ComponentState.OK


class PeopleDetector(Component):
    def __init__(self, name: str, config: ClientConfig, confidence_threshold: float) -> None:
        super().__init__(name)
        self.confidence_threshold = confidence_threshold
        self.detector_client = PeopleDetectorClient(config)

    @staticmethod
    def register_inputs(inputs: InputParams) -> None:
        inputs.declare("image", np.ndarray)

    @staticmethod
    def register_outputs(outputs: OutputParams) -> None:
        outputs.declare("detections", List[people_detection_pb2.Detection])

    async def forward(self) -> ComponentState:
        # get the image
        image: np.ndarray = await self.inputs.image.receive()

        # send data to the server
        detections: List[people_detection_pb2.Detection] = await self.detector_client.detect_people(
            image, self.confidence_threshold
        )

        # send the detections
        await self.outputs.detections.send(detections)
        return ComponentState.OK


class Visualization(Component):
    @staticmethod
    def register_inputs(inputs: InputParams) -> None:
        inputs.declare("image", np.ndarray)
        inputs.declare("detections", List[people_detection_pb2.Detection])

    async def forward(self) -> ComponentState:
        image, detections = await asyncio.gather(self.inputs.image.receive(), self.inputs.detections.receive())
        image_vis = image.copy()
        for det in detections:
            image_vis = cv2.rectangle(
                image_vis, (int(det.x), int(det.y)), (int(det.x + det.width), int(det.y + det.height)), (0, 255, 0), 2
            )

        cv2.namedWindow("image", cv2.WINDOW_NORMAL)
        cv2.resizeWindow('image', 1280, 800)
        cv2.imshow("image", image_vis)
        cv2.waitKey(1)

async def main(config_camera: ClientConfig, config_detector: ClientConfig) -> None:

    cam = AmigaCamera("amiga-camera", config_camera, stream_every_n=config_camera.stream_every_n)
    # NOTE: use the OpenCvCamera if you want to use a webcam
    # cam = OpenCvCamera("opencv-camera")
    detector = PeopleDetector("people-detector", config_detector, confidence_threshold=0.5)
    viz = Visualization("visualization")

    cam.outputs.image >> detector.inputs.image
    cam.outputs.image >> viz.inputs.image
    detector.outputs.detections >> viz.inputs.detections

    pipeline = Pipeline()
    pipeline.add_nodes([cam, detector, viz])

    await pipeline.async_run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="amiga-people-detector")
    parser.add_argument("--port-camera", type=int, required=True, help="The camera port.")
    parser.add_argument("--address-camera", type=str, default="localhost", help="The camera address")
    parser.add_argument("--port-detector", type=int, required=True, help="The camera port.")
    parser.add_argument("--address-detector", type=str, default="localhost", help="The camera address")
    parser.add_argument("--stream-every-n", type=int, default=5, help="Streaming frequency")
    args = parser.parse_args()

    # create the config for the clients
    config_camera = ClientConfig(port=args.port_camera, address=args.address_camera)
    config_camera.stream_every_n = args.stream_every_n

    config_detector = ClientConfig(port=args.port_detector, address=args.address_detector)

    # run the main
    asyncio.run(main(config_camera, config_detector))
