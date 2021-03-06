import numpy as np

from hexrd.ui.calibration.pick_based_calibration import run_calibration
from hexrd.ui.create_hedm_instrument import create_hedm_instrument
from hexrd.ui.constants import OverlayType
from hexrd.ui.hexrd_config import HexrdConfig
from hexrd.ui.line_picker_dialog import LinePickerDialog
from hexrd.ui.overlays import default_overlay_refinements
from hexrd.ui.utils import convert_tilt_convention, make_new_pdata


class CalibrationRunner:
    def __init__(self, canvas, parent=None):
        self.canvas = canvas
        self.parent = parent
        self.current_overlay_ind = -1
        self.all_overlay_picks = {}

    def run(self):
        self.validate()
        self.clear_all_overlay_picks()
        self.pick_next_line()

    def validate(self):
        visible_overlays = self.visible_overlays
        if not visible_overlays:
            raise Exception('No visible overlays')

        if not all(self.has_widths(x) for x in visible_overlays):
            raise Exception('All visible overlays must have widths')

        flags = HexrdConfig().get_statuses_instrument_format().tolist()
        # Make sure the length of our flags matches up with the instruments
        instr = create_hedm_instrument()
        if len(flags) != len(instr.calibration_flags):
            msg = ('Length of internal flags does not match '
                   'instr.calibration_flags')
            raise Exception(msg)

        # Add overlay refinements
        for overlay in visible_overlays:
            flags += [x[1] for x in self.get_refinements(overlay)]

        if np.count_nonzero(flags) == 0:
            raise Exception('There are no refinable parameters')

    def clear_all_overlay_picks(self):
        self.all_overlay_picks.clear()

    @staticmethod
    def has_widths(overlay):
        type = overlay['type']
        if type == OverlayType.powder:
            # Make sure the material has a two-theta width
            name = overlay['material']
            return HexrdConfig().material(name).planeData.tThWidth is not None
        elif type == OverlayType.laue:
            options = overlay.get('options', {})
            width_params = ['tth_width', 'eta_width']
            return all(options.get(x) is not None for x in width_params)
        elif type == OverlayType.mono_rotation_series:
            raise NotImplementedError('mono_rotation_series not implemented')
        else:
            raise Exception(f'Unknown overlay type: {type}')

    @property
    def overlays(self):
        return HexrdConfig().overlays

    @property
    def visible_overlays(self):
        return [x for x in self.overlays if x['visible']]

    @staticmethod
    def overlay_name(overlay):
        return f'{overlay["material"]} {overlay["type"].name}'

    def next_overlay(self):
        ind = self.current_overlay_ind
        ind += 1
        for i in range(ind, len(self.overlays)):
            if self.overlays[i]['visible']:
                self.current_overlay_ind = i
                return self.overlays[i]

    @property
    def active_overlay(self):
        if not 0 <= self.current_overlay_ind < len(self.overlays):
            return None

        return self.overlays[self.current_overlay_ind]

    @property
    def active_overlay_type(self):
        return self.active_overlay['type']

    def pick_next_line(self):
        overlay = self.next_overlay()
        if overlay is None:
            # No more overlays to do. Move on.
            self.finish()
            return

        # Create a backup of the visibilities that we will restore later
        self.backup_overlay_visibilities = self.overlay_visibilities

        # Only make the current overlay we are selecting visible
        self.set_exclusive_overlay_visibility(overlay)

        title = self.overlay_name(overlay)

        self.reset_overlay_picks()
        self.reset_overlay_data_index_map()
        self.increment_overlay_data_index()

        kwargs = {
            'canvas': self.canvas,
            'parent': self.canvas,
            'single_line_mode': overlay['type'] == OverlayType.laue
        }

        self._calibration_line_picker = LinePickerDialog(**kwargs)
        self._calibration_line_picker.ui.setWindowTitle(title)
        self._calibration_line_picker.start()
        self._calibration_line_picker.point_picked.connect(
            self.point_picked)
        self._calibration_line_picker.line_completed.connect(
            self.line_completed)
        self._calibration_line_picker.last_point_removed.connect(
            self.last_point_removed)
        self._calibration_line_picker.finished.connect(
            self.calibration_line_picker_finished)
        self._calibration_line_picker.result.connect(self.finish_line)

    def finish_line(self):
        self.save_overlay_picks()
        self.pick_next_line()

    def get_refinements(self, overlay):
        refinements = overlay.get('refinements')
        if refinements is None:
            refinements = default_overlay_refinements(overlay['type'])
        return refinements

    def generate_pick_results(self):

        def get_hkls(overlay):
            return {
                key: val.get('hkls', [])
                for key, val in overlay['data'].items()
            }

        pick_results = []
        for i, val in self.all_overlay_picks.items():
            overlay = self.overlays[i]
            pick_results.append({
                'material': overlay['material'],
                'type': overlay['type'].value,
                'options': overlay['options'],
                'refinements': self.get_refinements(overlay),
                'hkls': get_hkls(overlay),
                'picks': val
            })
        return pick_results

    @property
    def pick_materials(self):
        mats = [
            HexrdConfig().material(self.overlays[i]['material'])
            for i in self.all_overlay_picks
        ]
        return {x.name: x for x in mats}

    def dump_results(self):
        # This dumps out all results to files for testing
        # It is mostly intended for debug purposes
        import json
        import pickle

        class NumpyEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
                return json.JSONEncoder.default(self, obj)

        for name, mat in self.pick_materials.items():
            # Dump out the material
            mat_file_name = f'{name}.pkl'
            print(f'Writing out material to {mat_file_name}')
            with open(mat_file_name, 'wb') as wf:
                pickle.dump(mat, wf)

        pick_results = self.generate_pick_results()
        out_file = 'calibration_picks.json'
        print(f'Writing out picks to {out_file}')
        with open(out_file, 'w') as wf:
            json.dump(pick_results, wf, cls=NumpyEncoder)

        # Dump out the instrument as well
        instr = create_hedm_instrument()
        print(f'Writing out instrument to instrument.pkl')
        with open('instrument.pkl', 'wb') as wf:
            pickle.dump(instr, wf)

        # Dump out refinement flags
        flags = HexrdConfig().get_statuses_instrument_format()
        print(f'Writing out refinement flags to refinement_flags.json')
        with open('refinement_flags.json', 'w') as wf:
            json.dump(flags, wf, cls=NumpyEncoder)

    def finish(self):
        self.run_calibration()

    def run_calibration(self):
        picks = self.generate_pick_results()
        materials = self.pick_materials
        instr = create_hedm_instrument()
        flags = HexrdConfig().get_statuses_instrument_format()
        instr.calibration_flags = flags

        instr_calibrator = run_calibration(picks, instr, materials)
        self.write_instrument_to_hexrd_config(instr)

        # Update the lattice parameters and overlays
        overlays = [self.overlays[i] for i in self.all_overlay_picks]
        for overlay, calibrator in zip(overlays, instr_calibrator.calibrators):
            if calibrator.calibrator_type == 'powder':
                if calibrator.params.size == 0:
                    continue

                mat_name = overlay['material']
                mat = materials[mat_name]
                mat.latticeParameters = calibrator.params
                make_new_pdata(mat)
                HexrdConfig().flag_overlay_updates_for_material(mat_name)
                if mat is HexrdConfig().active_material:
                    HexrdConfig().active_material_modified.emit()
            elif calibrator.calibrator_type == 'laue':
                overlay['options']['crystal_params'] = calibrator.params

        # In case any overlays changed
        HexrdConfig().overlay_config_changed.emit()
        HexrdConfig().calibration_complete.emit()

    def write_instrument_to_hexrd_config(self, instr):
        iconfig = HexrdConfig().instrument_config_none_euler_convention

        # Add this so the calibration crystal gets written
        cal_crystal = iconfig.get('calibration_crystal')
        output_dict = instr.write_config(calibration_dict=cal_crystal)

        # Convert back to whatever convention we were using before
        eac = HexrdConfig().euler_angle_convention
        if eac is not None:
            convert_tilt_convention(output_dict, None, eac)

        # Add the saturation levels, as they seem to be missing
        sl = 'saturation_level'
        for det in output_dict['detectors'].keys():
            output_dict['detectors'][det][sl] = iconfig['detectors'][det][sl]

        # Save the previous iconfig to restore the statuses
        prev_iconfig = HexrdConfig().config['instrument']

        # Update the config
        HexrdConfig().config['instrument'] = output_dict

        # This adds in any missing keys. In particular, it is going to
        # add in any "None" detector distortions
        HexrdConfig().set_detector_defaults_if_missing()

        # Add status values
        HexrdConfig().add_status(output_dict)

        # Set the previous statuses to be the current statuses
        HexrdConfig().set_statuses_from_prev_iconfig(prev_iconfig)

    def set_exclusive_overlay_visibility(self, overlay):
        self.overlay_visibilities = [overlay is x for x in self.overlays]

    def calibration_line_picker_finished(self):
        self.pad_data_with_empty_lists()
        self.restore_backup_overlay_visibilities()
        self.remove_all_highlighting()

    def restore_backup_overlay_visibilities(self):
        self.overlay_visibilities = self.backup_overlay_visibilities
        HexrdConfig().overlay_config_changed.emit()

    def set_highlighting(self, highlighting):
        self.active_overlay['highlights'] = highlighting
        HexrdConfig().flag_overlay_updates_for_all_materials()
        HexrdConfig().overlay_config_changed.emit()

    def remove_all_highlighting(self):
        for overlay in self.overlays:
            if 'highlights' in overlay:
                del overlay['highlights']
        HexrdConfig().flag_overlay_updates_for_all_materials()
        HexrdConfig().overlay_config_changed.emit()

    @property
    def overlay_visibilities(self):
        return [x['visible'] for x in self.overlays]

    @overlay_visibilities.setter
    def overlay_visibilities(self, visibilities):
        for o, v in zip(self.overlays, visibilities):
            o['visible'] = v
        HexrdConfig().overlay_config_changed.emit()

    def reset_overlay_picks(self):
        self.overlay_picks = {}

    def reset_overlay_data_index_map(self):
        self.overlay_data_index = -1

        if self.active_overlay_type == OverlayType.powder:
            data_key = 'rings'
        elif self.active_overlay_type == OverlayType.laue:
            data_key = 'spots'
        else:
            raise Exception(f'{self.active_overlay_type} not implemented')

        data_map = {}
        ind = 0
        for key, value in self.active_overlay['data'].items():
            for i in range(len(value[data_key])):
                data_map[ind] = (key, data_key, i)
                ind += 1

        self.overlay_data_index_map = data_map

    def save_overlay_picks(self):
        # Make sure there is at least an empty list for each detector
        for key in self.active_overlay['data'].keys():
            if key not in self.overlay_picks:
                self.overlay_picks[key] = []
        self.all_overlay_picks[self.current_overlay_ind] = self.overlay_picks

    @property
    def current_data_path(self):
        idx = self.overlay_data_index
        if not 0 <= idx < len(self.overlay_data_index_map):
            return None

        return self.overlay_data_index_map[idx]

    @property
    def current_data_list(self):
        key, _, val = self.current_data_path
        root_list = self.overlay_picks.setdefault(key, [])
        if self.active_overlay_type == OverlayType.powder:
            while len(root_list) <= val:
                root_list.append([])
            return root_list[val]
        elif self.active_overlay_type == OverlayType.laue:
            # Only a single list for each Laue key
            return root_list

        raise Exception(f'Not implemented: {self.active_overlay_type}')

    def pad_data_with_empty_lists(self):
        # This increments the overlay data index to the end and inserts
        # empty lists along the way.
        if self.active_overlay_type == OverlayType.powder:
            while self.current_data_path is not None:
                # This will automatically insert a list for powder
                self.current_data_list
                self.overlay_data_index += 1
        elif self.active_overlay_type == OverlayType.laue:
            while self.current_data_path is not None:
                # Use NaN's to indicate a skip for laue
                self.current_data_list.append((np.nan, np.nan))
                self.overlay_data_index += 1

    def increment_overlay_data_index(self):
        self.overlay_data_index += 1
        data_path = self.current_data_path
        if data_path is None:
            # We are done picking for this overlay.
            if hasattr(self, '_calibration_line_picker'):
                self._calibration_line_picker.ui.accept()
            return

        self.set_highlighting([data_path])

        if self.active_overlay_type == OverlayType.powder:
            # Make sure a list is automatically inserted for powder
            self.current_data_list

    def decrement_overlay_data_index(self):
        if self.overlay_data_index == 0:
            # Can't go back any further
            return

        self.overlay_data_index -= 1
        data_path = self.current_data_path
        self.set_highlighting([data_path])

    def point_picked(self):
        linebuilder = self._calibration_line_picker.linebuilder
        data = (linebuilder.xs[-1], linebuilder.ys[-1])
        self.current_data_list.append(data)
        if self.active_overlay_type == OverlayType.laue:
            self.increment_overlay_data_index()

    def line_completed(self):
        self.increment_overlay_data_index()

    def last_point_removed(self):
        if self.active_overlay_type == OverlayType.powder:
            if len(self.current_data_list) == 0:
                # Go back one line
                self.decrement_overlay_data_index()
            if len(self.current_data_list) == 0:
                # Still nothing to do
                return
            # Remove the last point of data
            self.current_data_list.pop(-1)
        elif self.active_overlay_type == OverlayType.laue:
            self.decrement_overlay_data_index()
            if self.current_data_list:
                self.current_data_list.pop(-1)
