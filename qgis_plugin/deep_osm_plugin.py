from .qgis_2to3 import *
from .ui.dialogs import AboutDialog, SettingsDialog
from .log_helper import info
from .tile_helper import get_code_from_epsg, convert_coordinate, get_zoom_by_scale
import tempfile
import base64
import json
from .network_helper import post, post_async
from PyQt4.QtCore import QSettings


class DeepOsmPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.settings = QSettings("Vector Tile Reader", "vectortilereader")
        self.settings_dialog = SettingsDialog(self.settings)

    def initGui(self):
        self.about_action = self._create_action("About", "info.svg", self.show_about)
        self.detect_rectangles_action = self._create_action("Detect building area (rectangularized)", "group.svg", lambda: self.detect(True))
        self.detect_raw_action = self._create_action("Detect building area (raw)", "group.svg", lambda: self.detect(False))
        self.settings_action = self._create_action("Settings", "settings.svg", lambda: self.settings_dialog.show())

        self.popupMenu = QMenu(self.iface.mainWindow())
        self.popupMenu.addAction(self.detect_raw_action)
        self.popupMenu.addAction(self.detect_rectangles_action)
        self.popupMenu.addAction(self.settings_action)
        self.popupMenu.addAction(self.about_action)
        self.toolButton = QToolButton()
        self.toolButton.setMenu(self.popupMenu)
        self.toolButton.setDefaultAction(self.detect_raw_action)
        self.toolButton.setPopupMode(QToolButton.MenuButtonPopup)
        self.toolButtonAction = self.iface.layerToolBar().addWidget(self.toolButton)

    def _create_action(self, title, icon, callback, is_enabled=True):
        new_action = QAction(QIcon(':/plugins/deep_osm/{}'.format(icon)), title, self.iface.mainWindow())
        new_action.triggered.connect(callback)
        new_action.setEnabled(is_enabled)
        return new_action

    def _get_qgis_crs(self):
        canvas = self.iface.mapCanvas()
        return get_code_from_epsg(canvas.mapSettings().destinationCrs().authid())

    def _get_current_map_scale(self):
        canvas = self.iface.mapCanvas()
        current_scale = int(round(canvas.scale()))
        return current_scale

    def detect(self, rectangularize):
        extent = self.iface.mapCanvas().extent()
        qgis_crs = self._get_qgis_crs()

        lon_min, lat_min = convert_coordinate(qgis_crs, 3857, extent.yMinimum(), extent.xMinimum())
        lon_max, lat_max = convert_coordinate(qgis_crs, 3857, extent.yMaximum(), extent.xMaximum())
        scale = self._get_current_map_scale()
        zoom = get_zoom_by_scale(scale)

        info("extent @ zoom {}: {}", zoom, (lon_min, lat_min, lon_max, lat_max))
        temp_dir = os.path.join(tempfile.gettempdir(), "deep_osm")
        if not os.path.isdir(temp_dir):
            os.makedirs(temp_dir)
        file_path = os.path.join(temp_dir, "screenshot.png")
        self.iface.mapCanvas().saveAsImage(file_path, None, 'PNG')
        with open(file_path, 'rb') as f:
            binary_data = f.read()
        image_data = base64.standard_b64encode(binary_data)
        data = {
            'rectangularize': rectangularize,
            'x_min': lon_min,
            'x_max': lon_max,
            'y_min': lat_min,
            'y_max': lat_max,
            'zoom_level': zoom,
            'image_data': image_data
        }
        status, raw = post("http://localhost:8000/predict", json.dumps(data))
        if status == 200 and raw:
            response = {}
            try:
                response = json.loads(raw)
            except Exception as e:
                info("Parsing response failed: {}", str(e))
            if "features" in response:
                self.detection_finished(response["features"])
            else:
                info("Prediction failed: {}", response)

    def detection_finished(self, features):
        info("detection finished. {} features predicted", len(features))
        layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "Prediction", "memory")
        layer.setCrs(QgsCoordinateReferenceSystem(3857))
        layer.startEditing()

        for f in features:
            feature = QgsFeature()
            geom = QgsGeometry.fromWkt(f)
            if geom:
                feature.setGeometry(geom)
                layer.addFeature(feature, True)
        layer.commitChanges()
        layer.updateExtents()
        QgsMapLayerRegistry.instance().addMapLayer(layer)
        info("done")

    def unload(self):
        self.iface.layerToolBar().removeAction(self.toolButtonAction)

    @staticmethod
    def show_about():
        AboutDialog().show()
