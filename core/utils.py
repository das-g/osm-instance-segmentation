from PIL import Image
from itertools import groupby
from typing import Iterable, Tuple, List
from shapely.geometry import LineString, Point
from shapely.affinity import rotate, scale
from shapely import geometry
from skimage.draw import polygon_perimeter
from skimage.measure import find_contours
import cv2
import math
import numpy as np
import uuid

# numpy directions
UP = (-1, 0)
DOWN = (1, 0)
RIGHT = (0, 1)
LEFT = (0, -1)


class Line:
    def __init__(self, nr: int, p1: Tuple[float, float], p2: Tuple[float, float]):
        self._nr = nr
        self._p1 = p1
        self._p2 = p2
        self._length = LineString([p1, p2]).length
        self._orientation = None
        self._orthogonal = False
        self._neighbourhood = None

    def set_nr(self, nr: int):
        self._nr = nr

    def set_orientation(self, angle: float):
        self._orientation = angle

    def set_orthogonality(self, is_orthogonal: bool):
        self._orthogonal = is_orthogonal

    def set_neighbourhood(self, neighbourhood_id: uuid):
        self._neighbourhood = neighbourhood_id

    @property
    def neighbourhood(self) -> uuid:
        return self._neighbourhood

    @property
    def orthogonal(self) -> bool:
        return self._orthogonal

    @property
    def orientation(self) -> float:
        return self._orientation

    @property
    def nr(self) -> int:
        return self._nr

    @property
    def p1(self) -> Tuple[float, float]:
        return self._p1

    @property
    def p2(self) -> Tuple[float, float]:
        return self._p2

    @property
    def length(self) -> float:
        return self._length

    @property
    def coords(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        return self._p1, self._p2

    def distance(self, other) -> float:
        return geometry.LineString(self.coords).distance(geometry.LineString(other.coords))

    def __str__(self):
        return "Line(nr={}, coords={}, neighbourhood={})".format(self.nr, str(self.coords), str(self.neighbourhood)[:5])

    def __repr__(self):
        return self.__str__()


def get_main_orientation(contour, angle_in_degrees: bool = False, max_lines: int = None, max_threshold: int = 5):
    lines = None
    # ang = np.pi / 180 if angle is None else angle
    while True:
        new_lines = cv2.HoughLines(image=contour, rho=1, theta=np.pi / 180, threshold=max_threshold)
        if new_lines is not None:
            lines = new_lines
        else:
            max_threshold -= 1
            break
        if max_lines and len(lines) <= max_lines:
            break
        max_threshold += 1

    angles = {}
    maxcount = 0
    weighted_avg = None
    line = None
    if lines is not None:
        for l in lines:  # rho = distance, theta = angle
            for rho, theta in l:
                if angle_in_degrees:
                    angle = int(math.degrees(theta))
                else:
                    angle = theta
                if angle not in angles:
                    angles[angle] = 0
                newcount = angles[angle] + 1
                angles[angle] = newcount
                if newcount > maxcount:
                    maxcount = newcount

                a = np.cos(theta)
                b = np.sin(theta)
                x0 = a * rho
                y0 = b * rho
                x1 = int(x0 + 1000 * -b)
                y1 = int(y0 + 1000 * a)
                x2 = int(x0 - 1000 * -b)
                y2 = int(y0 - 1000 * a)
                line = Line(nr=0, p1=(x1, y1), p2=(x2, y2))

        angle_sum = 0
        counts = 0
        for a in angles:
            angle_sum += a * angles[a]
            counts += angles[a]
        weighted_avg = int(round(angle_sum / counts))
        weighted_avg %= 180 if angle_in_degrees else np.pi
    return line, weighted_avg


def make_lines_new(points: List[Tuple[float, float]], max_point_distance: float = 3, min_points_per_segment: int = 7,
                   hough_lines_threshold: int = 5) -> List[Line]:
    lines = []
    new_lines_added = True
    while new_lines_added and points:
        new_lines_added = False
        x, y = [i[0] for i in points], [i[1] for i in points]
        max_x, max_y = max(x), max(y)

        img = np.zeros((max_y + 1, max_x + 1), dtype=np.uint8)
        for x, y in points:
            img[y, x] = 1

        line, angle = get_main_orientation(img, angle_in_degrees=True, max_threshold=hough_lines_threshold)
        if line:
            ls = LineString(line.coords)
            first_line = []
            for p in points:
                if Point(p).distance(ls) <= max_point_distance:
                    first_line.append(p)

            while len(first_line) >= min_points_per_segment:
                segment = [first_line.pop()]
                neighbours = nearest_neighbours_recursive(segment[0], first_line)
                segment.extend(neighbours)
                if len(segment) >= min_points_per_segment:
                    new_lines_added = True
                    for p in segment:
                        points.remove(p)
                    segment = sorted(segment)
                    t_1 = ls.interpolate(ls.project(Point(segment[0])))
                    t_2 = ls.interpolate(ls.project(Point(segment[-1])))
                    line = Line(nr=1, p1=(t_1.x, t_1.y), p2=(t_2.x, t_2.y))
                    lines.append(line)
            # assert not len(first_line)

    if lines:
        lines_reordered = [lines.pop()]
        lines_reordered[0].set_nr(0)
        while lines:
            current_line = LineString(lines_reordered[-1].coords)
            nearest_line = None
            min_dist = None
            for l in lines:
                dist = current_line.distance(LineString(l.coords))
                if min_dist is None or dist < min_dist:
                    min_dist = dist
                    nearest_line = l
            lines.remove(nearest_line)

            nearest_line.set_nr(len(lines_reordered))
            lines_reordered.append(nearest_line)
        lines = lines_reordered

    return lines


def make_lines(points: List[Tuple[float, float]], point_distance_threshold: float = 2, min_line_length: float = 3) \
        -> List[Line]:
    """
     * Creates multiple line segments from the points of the contour.
    :param min_line_length: Lines with a length below this value will be ignored
    :param points:
    :param point_distance_threshold: Points up to a distance of this value will be considered neighbours
    :return:
    """

    points = points.copy()
    lines = []
    while points:
        seg = []
        point_ids = []
        while points and len(seg) < 3:
            seg.append(points.pop())
            point_ids.append(len(points))
        while True and points:
            err = root_mean_square_error(seg[-1], points[-1])
            if err <= point_distance_threshold:
                seg.append(points.pop())
                point_ids.append(len(points))
            else:
                break

        [vx, vy, x, y] = np.round(cv2.fitLine(points=np.asarray(seg, dtype=np.int32),
                                              distType=cv2.DIST_L2,
                                              param=0,
                                              reps=0.01,
                                              aeps=0.01), 2)
        if len(seg) >= 3:
            dist = geometry.Point(seg[0]).distance(geometry.Point(seg[-1]))
            x1 = float(x - dist / 2 * vx)
            x2 = float(x + dist / 2 * vx)
            y1 = float(y - dist / 2 * vy)
            y2 = float(y + dist / 2 * vy)
            line = Line(nr=len(lines), p1=(x1, y1), p2=(x2, y2))
            if line.length >= min_line_length:
                lines.append(line)
    return lines


def assign_orientation(lines: List[Line], angle_parallelity_threshold: float = 20) -> None:
    lines = lines.copy()
    lines = sorted(lines, key=lambda l: l.length)
    while lines:
        longest_line = lines.pop()
        main_angle = get_angle(longest_line.coords)
        longest_line.set_orientation(main_angle)
        for l in lines.copy():
            is_parallel, is_perpendicular = parallel_or_perpendicular(first_line=longest_line.coords,
                                                                      second_line=l.coords,
                                                                      threshold=angle_parallelity_threshold)
            if is_parallel or is_perpendicular:
                l.set_orthogonality(is_perpendicular)
                l.set_orientation(main_angle)
                lines.remove(l)


def update_neighbourhoods(lines: List[Line], window_size: int = 5, reassignment_threshold: float = 0.25) -> None:
    """
     * Tries to find lines which have been assigned to the wrong neighbourhood.
     > This can happen because initially the lines are only checked by its orientation. However, a single line of
       another orientation is probably misassigned if it's in the middle of another orientation-group.
    :param lines:
    :param window_size:
    :param reassignment_threshold:
    :return:
    """

    window_size = min(window_size, len(lines))
    sorted_by_nr = sorted(lines, key=lambda l: l.nr)
    for idx, _ in enumerate(sorted_by_nr):
        group = []
        while len(group) < window_size:
            group.append(sorted_by_nr[idx])
            idx = (idx + 1) % len(sorted_by_nr)
        orientation_lengths = {}
        total_length = 0
        for l in group:
            if l.orientation not in orientation_lengths:
                orientation_lengths[l.orientation] = 0
            orientation_lengths[l.orientation] += l.length
            total_length += l.length
        for ori in orientation_lengths:
            orientation_lengths[ori] = orientation_lengths[ori] / total_length
        most_probable_orientation = max(orientation_lengths, key=lambda l: orientation_lengths[l])
        lines_of_most_probable_orientation = list(
            filter(lambda l: l.orientation == most_probable_orientation, lines))
        most_probable_neighbourhood = lines_of_most_probable_orientation[0].neighbourhood
        master_line = list(sorted(lines_of_most_probable_orientation, key=lambda li: li.length * -1))[0]
        for ori in orientation_lengths:
            if orientation_lengths[ori] <= reassignment_threshold:
                lines_to_reassign = filter(lambda l: l.orientation == ori, group)
                for l in lines_to_reassign:
                    is_parallel, is_perpendicular = parallel_or_perpendicular(master_line.coords, l.coords)
                    l.set_orientation(most_probable_orientation)
                    l.set_neighbourhood(most_probable_neighbourhood)
                    l.set_orthogonality(not is_parallel)


def assign_neighbourhood(lines: List[Line], neighbour_distance_threshold: float = 15) -> None:
    """
     * Creates line clusters of neighbouring lines within each orientation group
    :param lines:
    :param neighbour_distance_threshold:
    :return:
    """

    all_neighbourhoods = []
    grouped_by_orientation = groupby(lines, key=lambda l: "{};{}".format(l.orientation, l.orthogonal))
    for angle, g in grouped_by_orientation:
        group = list(g)
        while group:
            neighbourhood = [group.pop()]
            new_neighbours = get_all_neighbours(neighbourhood[0], group, neighbour_distance_threshold)
            neighbourhood.extend(new_neighbours)
            all_neighbourhoods.append(neighbourhood)

    for neighbourhood in all_neighbourhoods:
        neighbourhood_id = uuid.uuid4()
        for line in neighbourhood:
            line.set_neighbourhood(neighbourhood_id)


def nearest_neighbours_recursive(point: Tuple[float, float], remaining_points: List[Tuple[float, float]])\
        -> List[Tuple[float, float]]:
    neighbours = []
    for p in remaining_points:
        if Point(point).distance(Point(p)) <= 2:
            neighbours.append(p)
            remaining_points.remove(p)
    for p in neighbours:
        new_neighbours = nearest_neighbours_recursive(p, remaining_points)
        neighbours.extend(new_neighbours)
    return neighbours


def get_all_neighbours(line: Line, remaining_lines: List[Line], neighbour_distance_threshold: float) -> List[Line]:
    """
     * Recursively finds all neighbours of the line and its neighbouring lines.
    :param line:
    :param remaining_lines:
    :param neighbour_distance_threshold:
    :return:
    """

    neighbours = []
    for l in remaining_lines:
        dist = line.distance(l)
        if 0 <= dist <= neighbour_distance_threshold:
            neighbours.append(l)
            remaining_lines.remove(l)
    for n in neighbours.copy():
        new_neighbours = get_all_neighbours(n, remaining_lines, neighbour_distance_threshold)
        neighbours.extend(new_neighbours)
    return neighbours


def get_angle(first_line: Tuple[Tuple[float, float], Tuple[float, float]],
              second_line: Tuple[Tuple[float, float], Tuple[float, float]] = None) -> float:
    """
     * Measures the angle between a horizontal line and the line defined by p1 and p2.
       The angle is in the range: 0 <= x < 180
    :return:
    """
    if not second_line:
        second_line = ((0, 0), (1, 0))  # horizontal vector (numpy indexing)
    v1 = np.array(second_line[0][::-1]) - np.array(second_line[1][::-1])
    v0 = np.array(first_line[0][::-1]) - np.array(first_line[1][::-1])
    angle = np.math.atan2(np.linalg.det([v0, v1]), np.dot(v0, v1))
    deg = np.degrees(angle) % 180
    return deg


def parallel_or_perpendicular(first_line: Tuple[Tuple[float, float], Tuple[float, float]],
                              second_line: Tuple[Tuple[float, float], Tuple[float, float]] = None,
                              threshold: float = 20) -> Tuple[bool, bool]:
    ang = get_angle(first_line, second_line)
    is_parallel = 0 <= min(ang, math.fabs(ang - 180)) <= threshold
    is_perpendicular = 90 - threshold <= ang <= 90 + threshold
    return is_parallel, is_perpendicular


def root_mean_square_error(p1, p2) -> float:
    mean_x = (p1[0] - p2[0]) ** 2
    mean_y = (p1[1] - p2[1]) ** 2
    return math.sqrt(1 / 2 * (mean_x + mean_y))


def get_reoriented_lines(lines: List[Line]) -> List[Line]:
    outline = []
    grouped_orientation = groupby(lines, key=lambda l: l.orientation)
    for k, g in grouped_orientation:
        subgroup = list(g)
        for seg in subgroup:
            nr = seg.nr

            current_angle = get_angle((seg.p1, seg.p2))
            if not seg.orthogonal:
                target_angle = k
            else:
                target_angle = (k + 90)

            angle_delta = target_angle - current_angle
            angle_delta = min(angle_delta, angle_delta % 180)
            ls = rotate(geometry.LineString(seg.coords), angle_delta)

            xs = ls.coords.xy[0]
            ys = ls.coords.xy[1]

            fitted_line_new = [(xs[0], ys[0]), (xs[1], ys[1])]

            new_line = Line(nr, *fitted_line_new)
            new_line.set_orientation(target_angle)
            outline.append(new_line)

            # This step is important: The points are arranged in CCW order
    if not outline:
        lines_reordered = []
    else:
        lines_reordered = [outline.pop()]
        while outline:
            line = outline.pop()
            p_1 = min(line.coords, key=lambda p: geometry.Point(p).distance(geometry.Point(lines_reordered[-1].p2)))
            p_2 = line.coords[1] if p_1 == line.coords[0] else line.coords[0]
            next_line = Line(line.nr, p_1, p_2)
            next_line.set_orientation(line.orientation)
            lines_reordered.append(next_line)
    return lines_reordered


def remove_redundant_segments(outline: List[Line]) -> None:
    i = 0
    while outline and i <= len(outline):
        line = outline[i % len(outline)]
        next_line = outline[(i + 1) % len(outline)]
        parallel = line.orientation == next_line.orientation and line.orthogonal == next_line.orthogonal
        if parallel:
            ls_1 = LineString(line.coords)
            ls_2 = LineString(next_line.coords)
            ls_1_scaled = scale(ls_1, 1000, 1000)
            ls_2_scaled = scale(ls_2, 1000, 1000)
            redundant = ls_1_scaled.buffer(1).intersects(ls_2_scaled.buffer(1))
            if redundant:
                outline.remove(next_line)
            else:
                i += 1
        else:
            i += 1


def get_contours(masks: np.ndarray) -> List[List[Tuple[int, int]]]:
    contours = []
    for i in range(masks.shape[-1]):
        mask = masks[:, :, i]
        points = get_contour(mask)
        if points:
            contours.append(points)
    return contours


def get_contour(mask: np.ndarray) -> List[Tuple[int, int]]:
    points = []
    try:
        if mask.any():
            conts = find_contours(mask, 0.5)
            for c in conts:
                rr, cc = polygon_perimeter(c[:, 0], c[:, 1], shape=mask.shape, clip=False)
                points = tuple(zip(cc, rr))
    except:
        print("Error during contour extraction. Empty contour will be returned")
    return points


def get_corner_points(outline: List[Line]) -> List[Tuple[float, float]]:
    corner_points = []
    if not outline:
        return corner_points

    for i, line in enumerate(outline):
        next_line = outline[(i + 1) % len(outline)]
        parallel = line.orientation == next_line.orientation and line.orthogonal == next_line.orthogonal
        ls_1 = LineString(line.coords)
        ls_2 = LineString(next_line.coords)
        if ls_1.intersects(ls_2):
            p = ls_1.intersection(ls_2)
            _add_coords(p, corner_points)
        elif parallel:
            ls_middle = LineString([line.coords[-1], next_line.coords[0]])
            center = ls_middle.centroid
            center_line = scale(rotate(geom=LineString([center, (center.x + 10, center.y)]),
                                       angle=line.orientation + 90,
                                       origin=center), 10, 10)

            p_1 = scale(ls_1, 1000, 1000).intersection(center_line)
            p_2 = scale(ls_2, 1000, 1000).intersection(center_line)
            _add_coords(p_1, corner_points)
            if p_2 != p_1:
                _add_coords(p_2, corner_points)
        else:
            p = scale(ls_1, 1000, 1000).intersection(scale(ls_2, 1000, 1000))
            if isinstance(p, Point):
                corner_points.append((p.x, p.y))

    corner_points.append(corner_points[0])
    return corner_points


def _add_coords(geom, coords: List[Tuple[float, float]]) -> None:
    if isinstance(geom, geometry.Point):
        coords.append((geom.x, geom.y))
    elif isinstance(geom, LineString):
        coords.extend(geom.coords)
    else:
        pass
        # raise RuntimeError("Invalid intersection result: ", geom, coords)


def rectangularize(contour_points: List[Tuple[int, int]]) -> List[Tuple[float, float]]:
    # Tolerance for initial douglas-peucker run
    approximization_tolerance = 0.01

    # All points with a max distance from each other will be added to the same line
    point_distance_threshold = 1

    # Lines below this length will be discarded
    min_points_per_segment = 7

    # Angles with a difference up to this value will be considered parallel
    angle_parallelity_threshold = 20

    # Threshold for the hough-algorithm when finding the lines
    hough_lines_threshold = 5

    # Lines located at a distance up to this value will be considered neighbours
    neighbour_distance_threshold = 10

    # Neighbour reassignment: A sliding window will be moved around the contour to detect wrong assignments
    # Nr. of segments per window
    window_size = 5

    # If the probability of a segment to its class is below this threshold,
    # it will be reassigned to the most probable class
    reassignment_threshold = 0.25

    lines = make_lines_new(contour_points,
                           max_point_distance=point_distance_threshold,
                           min_points_per_segment=min_points_per_segment,
                           hough_lines_threshold=hough_lines_threshold)
    assign_orientation(lines, angle_parallelity_threshold=angle_parallelity_threshold)
    assign_neighbourhood(lines, neighbour_distance_threshold=neighbour_distance_threshold)
    update_neighbourhoods(lines, window_size=window_size, reassignment_threshold=reassignment_threshold)
    remove_redundant_segments(lines)
    lines = get_reoriented_lines(lines)
    corner_points = get_corner_points(lines)
    # p = geometry.Polygon(corner_points).simplify(1)
    # corner_points = list(map(lambda c: (c[0], c[1]), p.exterior.coords))
    return corner_points


class MarchingSquares:
    """
      Implementation of the marching square algorithm to find contours on images. O
      The current implementation finds only one contour per image (the one top-left, to be exact).
    """

    BORDER_SIZE = 1

    def __init__(self, data: np.ndarray):
        c = data.copy()
        self.img = np.pad(c, pad_width=self.BORDER_SIZE, mode='constant')
        self._contour = np.zeros(self.img.shape, dtype=np.uint8)
        self._states = np.zeros(self.img.shape, dtype=np.uint8)
        self._start = None
        self._marched = False
        self._points = []

    @classmethod
    def from_file(cls, img_path: str):
        img = Image.open(img_path).convert("L")
        np_arr = np.asarray(img)
        return cls(np_arr)

    @classmethod
    def from_array(cls, data: np.ndarray):
        return cls(data)

    def find_contour(self) -> Iterable[Tuple[int, int]]:
        """
         * Returns the first contour found.
        :return:
        """

        self._calc_cell_states()
        points = []
        self._marched = True
        if self._start:
            self._contour[self._sum_tuple(self._start, (1, 1))] = 1
            points.append(self._start[::-1])
            current_pos = None
            while current_pos != self._start:
                if not current_pos:
                    current_pos = self._start

                state = self._states[current_pos]
                direction = self._get_next_direction(state)

                current_pos = self._sum_tuple(current_pos, direction)
                flipped = current_pos[::-1]
                if current_pos != self._start and flipped in points:
                    raise RuntimeError("Invalid contour")
                self._contour[self._sum_tuple(current_pos, (1, 1))] = 255
                points.append(flipped)

        self._contour = np.zeros(self.img.shape, dtype=np.uint8)
        for x, y in points:
            self._contour[y, x] = 1

        self._points = points
        return self._points

    @staticmethod
    def _sum_tuple(t1: Tuple, t2: Tuple) -> Tuple:
        return tuple(map(sum, zip(t1, t2)))

    @property
    def exact_contour(self):
        return self._contour

    def _get_main_orientation(self, angle: float = None, angle_in_degrees: bool = False, max_lines: int = None) \
            -> Tuple[float, Tuple[float, float]]:
        max_threshold = 5
        lines = None
        # ang = np.pi / 180 if angle is None else angle
        while True:
            if angle:
                new_lines = cv2.HoughLines(image=self.exact_contour, rho=1, theta=np.pi / 180, threshold=max_threshold,
                                           min_theta=angle, max_theta=angle)
            else:
                new_lines = cv2.HoughLines(image=self.exact_contour, rho=1, theta=np.pi / 180, threshold=max_threshold)
            if new_lines is not None:
                lines = new_lines
            else:
                max_threshold -= 1
                break
            if max_lines and len(lines) <= max_lines:
                break
            max_threshold += 1

        angles = {}
        maxcount = 0
        nearest_point = None
        weighted_avg = None
        if lines is not None:
            for l in lines:  # rho = distance, theta = angle
                for rho, theta in l:
                    if angle_in_degrees:
                        angle = int(math.degrees(theta))
                    else:
                        angle = theta
                    if angle not in angles:
                        angles[angle] = 0
                    newcount = angles[angle] + 1
                    angles[angle] = newcount
                    if newcount > maxcount:
                        maxcount = newcount

            angle_sum = 0
            counts = 0
            for a in angles:
                angle_sum += a * angles[a]
                counts += angles[a]
            weighted_avg = int(round(angle_sum / counts))
            weighted_avg %= 180 if angle_in_degrees else np.pi
        return weighted_avg, nearest_point

    def main_orientation(self, angle_in_degrees: bool = False) -> Tuple[int, geometry.Point]:
        if not self._marched:
            raise RuntimeError("To get the main orientation, run 'find_contour' first.")

        nearest_point = None
        weighted_avg = self._get_main_orientation(angle_in_degrees=angle_in_degrees)
        return weighted_avg, nearest_point

    @staticmethod
    def _get_next_direction(state: int) -> Tuple[int, int]:
        """
         * These directions will lead to a clockwise visit of the contour
        :param state:
        :return:
        """
        if state in [1, 5, 9, 13]:
            return DOWN
        elif state in [2, 3, 11]:
            return RIGHT
        elif state in [4, 6, 7, 10]:
            return UP
        elif state in [8, 12, 14]:
            return LEFT
        raise RuntimeError("Illelgal state: {}".format(state))

    def _calc_cell_states(self) -> None:
        for (r, c), value in np.ndenumerate(self.img[:-1, :-1]):
            top_left = value > 0
            top_right = self.img[r, c + 1] > 0
            bottom_right = self.img[r + 1, c + 1] > 0
            bottom_left = self.img[r + 1, c] > 0
            cell_state = (top_left << 3) | (top_right << 2) | (bottom_right << 1) | bottom_left
            self._states[r, c] = cell_state
            if not self._start and 0 < cell_state < 15:
                self._start = (r, c)


def _get_abs(p: Tuple[float, float], offset_x, offset_y, per_pixel_width, per_pixel_height) -> Tuple[float, float]:
    return offset_x + p[0] * per_pixel_width, offset_y + p[1] * per_pixel_height


def georeference(points: Iterable[Tuple[float, float]], extent) -> Iterable[Tuple]:
    min_x = extent['x_min']
    min_y = extent['y_min']
    max_y = extent['y_max']
    max_x = extent['x_min'] + 256.0 * (extent['x_max'] - min_x) / extent['img_width']
    min_y = max_y + 256.0 * (extent['y_max'] - min_y) / extent['img_height']
    per_pixel_width = (max_x - min_x) / 256.0
    per_pixel_height = (max_y - min_y) / 256.0

    georeferenced = []
    try:
        georeferenced = list(map(lambda p: _get_abs(p, min_x, max_y, per_pixel_width, per_pixel_height), points))
    except:
        print("invalid outline: ", points)
        # raise RuntimeError("Invalid outline: ", points)
    return georeferenced
