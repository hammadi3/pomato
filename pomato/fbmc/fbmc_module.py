"""

THIS IS FBMC Module
INPUT: DATA
OUTPUT: RESULTS

"""

import logging
import datetime as dt
import numpy as np
import pandas as pd

import matplotlib.pyplot as plt
from scipy import spatial
import pomato.tools as tools
from pomato.cbco import CBCOModule

# kriterien für cb auswahl
# # n-0 last
# # lodf filter vll die ersten 10
# #
# # häufigkeit als teil der domain
# #
# # 3d plot

class FBMCModule():
    """ Class to do all calculations in connection with cbco calculation"""
    def __init__(self, wdir, grid_object, data, basecase_name=None, cbco_list=None):
        # Import Logger
        self.logger = logging.getLogger('Log.fbmc.FBMCModule')
        self.logger.info("Initializing the FBMCModule....")

        self.wdir = wdir

        self.grid = grid_object
        self.nodes = grid_object.nodes
        self.lines = grid_object.lines

        self.flowbased_region = ["DE", "FR", "NL", "BE", "LU"]
        self.cbco_list = cbco_list

        self.data = data
        if not data.results:
            self.logger.error("No results not initialzed!")
        elif len(data.results) == 1:
            self.basecase = data.results[next(r for r in list(data.results))]
        elif len(data.results) > 1 and basecase_name:
            self.basecase = data.results[basecase_name]
        else:
            self.logger.error("More results available as basecase. Please specify argument basecase_name!")

        self.nodal_fbmc_ptdf, self.domain_info = self.create_fbmc_info()

        self.logger.info("FBMCModule  Initialized!")

    def create_gsk(self, option="flat"):
        """returns GSK, either flat or gmax"""
        gsk = pd.DataFrame(index=self.nodes.index)

        plant_types = self.basecase.data.options["optimization"]["plant_types"]
        condition = (~self.basecase.data.plants.plant_type.isin(plant_types["ts"])) & \
                    (~self.basecase.data.plants.plant_type.isin(plant_types["es"]))
        
        gmax_per_node = self.basecase.data.plants.loc[condition, ["g_max", "node"]] \
                        .groupby("node").sum()

        for zone in self.basecase.data.zones.index:
            nodes_in_zone = self.nodes.index[self.nodes.zone == zone]
            gsk[zone] = 0
            gmax_in_zone = gmax_per_node[gmax_per_node.index.isin(nodes_in_zone)]
            if option == "gmax":
                if not gmax_in_zone.empty:
                    gsk_value = gmax_in_zone.g_max/gmax_in_zone.values.sum()
                    gsk.loc[gsk.index.isin(gmax_in_zone.index), zone] = gsk_value
                else:
                    gsk.loc[gsk.index.isin(nodes_in_zone), zone] = 1/len(nodes_in_zone)

            elif option == "flat":
                gsk.loc[gsk.index.isin(nodes_in_zone), zone] = 1/len(nodes_in_zone)

        return gsk.values

    def return_critical_branches(self, threshold=5e-2, gsk_strategy="gmax"):

        self.logger.info("List of CBs is generated from zone-to-zone PTDFs with:")
        self.logger.info("GSK Strategy: %s, Threshold: %d percent", gsk_strategy, threshold*100)

        gsk = self.create_gsk(gsk_strategy)
        zonal_ptdf = np.dot(self.grid.ptdf, gsk)
        zonal_ptdf_df = pd.DataFrame(index=self.lines.index,
                                     columns=self.basecase.data.zones.index,
                                     data=zonal_ptdf)

        z2z_ptdf_df = pd.DataFrame(index=self.lines.index)
        for zone in self.flowbased_region:
            for zzone in self.flowbased_region:
                z2z_ptdf_df["-".join([zone, zzone])] = zonal_ptdf_df[zone] - zonal_ptdf_df[zzone]

        critical_branches = list(z2z_ptdf_df.index[np.any(z2z_ptdf_df.abs() > threshold, axis=1)])

        condition_cross_border = self.nodes.zone[self.lines.node_i].values != \
                                 self.nodes.zone[self.lines.node_j].values

        cond_fb_region = self.nodes.zone[self.lines.node_i].isin(self.flowbased_region).values & \
                         self.nodes.zone[self.lines.node_j].isin(self.flowbased_region).values

        cross_border_lines = list(self.lines.index[condition_cross_border&cond_fb_region])
        total_cbs = list(set(critical_branches + cross_border_lines))

        self.logger.info("Number of Critical Branches: %d, \
                          Number of Cross Border lines: %d, \
                          Total Number of CBs: %d",
                          len(critical_branches), len(cross_border_lines), len(total_cbs))

        return total_cbs

    def create_fbmc_info(self, lodf_sensitivity=10e-2):

        """
        create ptdf, determine CBs
        """

        if isinstance(self.cbco_list, pd.DataFrame):
            base_cb = list(self.cbco_list.cb[self.cbco_list.co == "basecase"])

            index_position = [self.lines.index.get_loc(line) for line in base_cb]
            base_ptdf = self.grid.ptdf[index_position, :]
            full_ptdf = [base_ptdf, -base_ptdf]
            label_lines = list(base_cb)+list(base_cb)
            label_outages = ["basecase" for line in label_lines]

            self.cbco_list = self.cbco_list[~(self.cbco_list.co == "basecase")]
            select_lines = []
            select_outages = {}
            for line in self.cbco_list.cb.unique():
                select_lines.append(line)
                select_outages[line] = list(self.cbco_list.co[self.cbco_list.cb == line])

        else:
            self.lines["cb"] = False
            critical_branches = self.return_critical_branches(threshold=5e-2)
            self.lines.loc[self.lines.index.isin(critical_branches), "cb"] = True

            select_lines = self.lines.index[(self.lines["cb"])&(self.lines.contingency)]
            select_outages = {}
            for line in select_lines:
                select_outages[line] = list(self.grid.lodf_filter(line, lodf_sensitivity))

            index_position = [self.lines.index.get_loc(line) for line in select_lines]
            base_ptdf = self.grid.ptdf[index_position, :]
            full_ptdf = [base_ptdf, -base_ptdf]
            label_lines = list(select_lines)+list(select_lines)
            label_outages = ["basecase" for line in label_lines]

        for line in select_lines:
            outages = select_outages[line]
            tmp_ptdf = np.vstack([self.grid.create_n_1_ptdf_cbco(line, out) for out in outages])
            full_ptdf.extend([tmp_ptdf, -tmp_ptdf])
            label_lines.extend([line for i in range(0, 2*len(outages))])
            label_outages.extend(outages*2)

        nodal_fbmc_ptdf = np.concatenate(full_ptdf)
        nodal_fbmc_ptdf = nodal_fbmc_ptdf.reshape(len(label_lines), len(list(self.nodes.index)))

        domain_info = pd.DataFrame(columns=list(self.basecase.data.zones.index))
        domain_info["cb"] = label_lines
        domain_info["co"] = label_outages

        return nodal_fbmc_ptdf, domain_info

    def create_flowbased_ptdf(self, gsk_strategy, timestep):
        """
        Create Zonal ptdf -> creates both positive and negative line
        restrictions or ram. Depending on flow != 0
        """
        self.logger.info("Creating zonal Ab")
        # Calculate zonal ptdf based on ram -> (if current flow is 0 the
        # zonal ptdf is based on overall
        # avalable line capacity (l_max)), ram is calculated for every n-1
        # ptdf matrix to ensure n-1 security constrained FB Domain
        # The right side of the equation has to be positive

        frm_fav = pd.DataFrame(index=self.domain_info.cb.unique())
        frm_fav["value"] = self.lines.maxflow[frm_fav.index]*.2

        injection = self.basecase.INJ.INJ[self.basecase.INJ.t == timestep].values

        f_ref_base_case = np.dot(self.nodal_fbmc_ptdf, injection)
        gsk = self.create_gsk(gsk_strategy)
        zonal_fbmc_ptdf = np.dot(self.nodal_fbmc_ptdf, gsk)

        # F Day Ahead (eigentlich mit LTNs)
        net_position = self.basecase.net_position() * 1
        # net_position.loc[:, ~net_position.columns.isin(self.flowbased_region)] = 0

        f_da = np.dot(zonal_fbmc_ptdf, net_position.loc[timestep].values)
        # f_ref_nonmarket = f_ref_base_case
        f_ref_nonmarket = f_ref_base_case - f_da

        capacity_multiplier = self.basecase.data.options["grid"]["capacity_multiplier"]

        ram = np.subtract(self.lines.maxflow[self.domain_info.cb]/capacity_multiplier - \
                          frm_fav.value[self.domain_info.cb],
                          f_ref_nonmarket).values
        ram = ram.reshape(len(ram), 1)

        if any(ram < 0):
            self.logger.warning("Number of RAMs below: [0 - %d, 10 - %d, 100 - %d, 1000 - %d]", sum(ram<0), sum(ram<10), sum(ram<100), sum(ram<1000))
            ram[ram<100] = 10000


        self.domain_info[list(self.basecase.data.zones.index)] = zonal_fbmc_ptdf
        self.domain_info["ram"] = ram
        self.domain_info["timestep"] = timestep
        self.domain_info["gsk_strategy"] = gsk_strategy
        self.logger.info("Done!")

        # return A, b
        return zonal_fbmc_ptdf, ram

    def create_fbmc_equations(self, domain_x, domain_y, A, b):
        """
        from zonal ptdf calculate linear equations ax = b to plot the FBMC domain
        nodes/Zones that are not part of the 2D FBMC are summerized using GSK sink
        """
        self.logger.info("Creating fbmc equations...")
        list_zones = list(self.nodes.zone.unique())
        if len(domain_x) == 2:
            domain_idx = [[list_zones.index(zone[0]),
                           list_zones.index(zone[1])] for zone in [domain_x, domain_y]]
            A = np.vstack([np.dot(A[:, domain], np.array([1, -1])) for domain in domain_idx]).T
        else:
            self.logger.warning("Domains not set in the right way!")
            raise

        #Clean reduce Ax=b only works if b_i != 0 for all i,
        #which should be but sometimes wierd stuff comes up
        #Therefore if b == 0, b-> 1 (or something small>0)
        if not (b > 0).all():
            b[(b < 0)] = 0.1
            self.logger.warning('some b is not right (possibly < 0)')

        return(A, b)

    def create_flowbased_parameters(self):

        fbmc_paramters = {}
        cbco = CBCOModule(self.wdir, self.grid, self.data, self.data.options)
        cbco.options["optimization"]["type"] = "cbco_zonal"
        cbco.options["grid"]["cbco_option"] = "clarkson"
        for timestep in self.basecase.INJ.t.unique():
            cbco.A, cbco.b = self.create_flowbased_ptdf("gmax", timestep)
            cbco.cbco_info = self.domain_info
            cbco.x_bounds = np.array([])
            cbco.cbco_index = np.array([])
            cbco.cbco_index = cbco.clarkson_algorithm()
            fbmc_paramters[timestep] = cbco.return_cbco()

        fbmc_rep = pd.concat([fbmc_paramters[t] for t in fbmc_paramters.keys()], ignore_index=True)
        fbmc_rep.set_index(fbmc_rep.cb + "_" + fbmc_rep.co, inplace=True)
        
        return fbmc_rep



