import copy

from PySide2.QtCore import QObject

from hexrd import spacegroup

from hexrd.ui.ui_loader import UiLoader
from hexrd.ui import utils


class AddMaterialDialog(QObject):

    def __init__(self, parent=None):
        super(AddMaterialDialog, self).__init__(parent)

        loader = UiLoader()
        self.ui = loader.load_file('add_material_dialog.ui', parent)

        # Don't create a material until later to avoid the expensive
        # _newPdata() operation at startup.
        self.material = None

        self.setup_space_group_widgets()

        self.setup_connections()

    def setup_connections(self):
        for widget in self.lattice_widgets:
            widget.valueChanged.connect(self.set_lattice_params)

        for widget in self.space_group_setters:
            widget.currentIndexChanged.connect(self.set_space_group)
            widget.currentIndexChanged.connect(self.enable_lattice_params)

        self.ui.material_name.textChanged.connect(self.set_name)

    def setup_space_group_widgets(self):
        for k in spacegroup.sgid_to_hall:
            self.ui.space_group.addItem(k)
            self.ui.hall_symbol.addItem(spacegroup.sgid_to_hall[k])
            self.ui.hermann_mauguin.addItem(spacegroup.sgid_to_hm[k])

    def update_gui_from_material(self):
        key_list = [x[0] for x in spacegroup.sgid_to_hall.items()]
        key_list = [x.split(':')[0] for x in key_list]
        sgid = key_list.index(str(self.material.sgnum))

        self.set_space_group(sgid)
        self.enable_lattice_params()  # This updates the values also
        self.ui.max_hkl.setValue(self.material.hklMax)
        self.ui.material_name.setText(self.material.name)

    @property
    def lattice_widgets(self):
        return [
            self.ui.lattice_a,
            self.ui.lattice_b,
            self.ui.lattice_c,
            self.ui.lattice_alpha,
            self.ui.lattice_beta,
            self.ui.lattice_gamma
        ]

    @property
    def space_group_setters(self):
        return [
            self.ui.space_group,
            self.ui.hall_symbol,
            self.ui.hermann_mauguin
        ]

    def block_lattice_signals(self, block=True):
        for widget in self.lattice_widgets:
            widget.blockSignals(block)

    def block_sgs_signals(self, block=True):
        for widget in self.space_group_setters:
            widget.blockSignals(block)

    def set_space_group(self, val):
        self.block_sgs_signals(True)
        try:
            self.ui.space_group.setCurrentIndex(val)
            self.ui.hall_symbol.setCurrentIndex(val)
            self.ui.hermann_mauguin.setCurrentIndex(val)
            sgid = int(self.ui.space_group.currentText().split(':')[0])

            # This can be an expensive operation, so make sure it isn't
            # already equal before setting.
            if self.material.sgnum != sgid:
                self.material.sgnum = sgid

            for sgids, lg in spacegroup._pgDict.items():
                if sgid in sgids:
                    self.ui.laue_group.setText(lg[0])
                    break
            self.ui.lattice_type.setText(spacegroup._ltDict[lg[1]])
        finally:
            self.block_sgs_signals(False)

    def enable_lattice_params(self):
        """enable independent lattice parameters"""
        # lattice parameters are stored in the old "ValUnit" class
        self.block_lattice_signals(True)
        try:
            m = self.material
            sgid = int(self.ui.space_group.currentText().split(':')[0])

            # This can be an expensive operation, so make sure it isn't
            # already equal before setting.
            if self.material.sgnum != sgid:
                self.material.sgnum = sgid

            reqp = m.spaceGroup.reqParams
            lprm = m.latticeParameters
            for i, widget in enumerate(self.lattice_widgets):
                widget.setEnabled(i in reqp)
                u = 'angstrom' if i < 3 else 'degrees'
                widget.setValue(lprm[i].getVal(u))
        finally:
            self.block_lattice_signals(False)

    def set_lattice_params(self):
        """update all the lattice parameter boxes when one changes"""
        # note: material takes reduced set of lattice parameters but outputs
        #       all six
        self.block_lattice_signals(True)
        try:
            m = self.material
            reqp = m.spaceGroup.reqParams
            nreq = len(reqp)
            lp_red = nreq*[0.0]
            for i in range(nreq):
                boxi = self.lattice_widgets[reqp[i]]
                lp_red[i] = boxi.value()
            m.latticeParameters = lp_red
            lprm = m.latticeParameters
            for i, widget in enumerate(self.lattice_widgets):
                u = 'angstrom' if i < 3 else 'degrees'
                widget.setValue(lprm[i].getVal(u))
        finally:
            self.block_lattice_signals(False)

    def set_name(self, name):
        self.material.name = name

    def hexrd_material(self):
        """Use the UI selections to create a new hexrd material"""

        # Everything should be set for self.material except hkl
        # This will create a new planeData object as well.
        # Although this may be expensive, it is necessary to create
        # a new planeData object in case the lattice parameters changed.
        self.material.hklMax = self.ui.max_hkl.value()

        utils.fix_exclusions(self.material)
        return copy.deepcopy(self.material)

    def set_material(self, mat, new_name=None):
        self.material = copy.deepcopy(mat)

        if new_name is not None:
            self.material.name = new_name

        self.update_gui_from_material()
