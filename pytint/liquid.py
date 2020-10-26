"""
Contains methods for liquid
"""

import numpy as np
import yaml
from pytint.integrators import *
import pyscal.core as pc
import pyscal.traj_process as ptp
from pylammpsmpi import LammpsLibrary

class Liquid:
    """
    Liquid class
    """
    def __init__(self, t=None, p=None, l=None, apc=None,
                    alat=None, c=None, options=None, simfolder=None,
                    thigh=None):
        """
        Set up class
        """
        self.t = t
        self.p = p
        self.l = l
        self.apc = apc
        self.alat = alat
        self.c = c
        self.options = options
        self.simfolder = simfolder
        self.thigh = thigh

    def write_average_script(self):
        """
        Write averagin script for solid
        """
        cores = self.options["queue"]["cores"]

        #create lammps object
        lmp = LammpsLibrary(mode="local", cores=cores)

        #set up units
        lmp.command("units            metal")
        lmp.command("boundary         p p p")
        lmp.command("atom_style       atomic")
        lmp.command("timestep"         ,self.options["md"]["timestep"])

        #set up structure
        lmp.command("lattice"         self.l, self.alat)
        lmp.command("region           box block" 0, self.options["md"]["nx"], 0, self.options["md"]["ny"], 0, self.options["md"]["nz"])
        lmp.command("create_box       1 box")
        lmp.command("create_atoms     1 box")

        #set up potential
        lmp.command("pair_style"       ,self.options["md"]["pair_style"])
        lmp.command("pair_coeff"       , self.options["md"]["pair_coeff"])
        lmp.command("mass             *", self.options["md"]["mass"])

        #Melt regime for the liquid
        lmp.command("velocity         all create", self.thigh, np.random.randint(0, 10000))
        lmp.command("fix              1 all npt temp", self.thigh, self.thigh, self.options["md"]["tdamp"], 
                                      "iso", self.p, self.p, self.options["md"]["pdamp"])
        lmp.command("run              ", int(self.options["md"]["nsmall"]))
        lmp.command("unfix            1")
        lmp.command("dump             2 all custom", 1, "traj.melt id type mass x y z vx vy vz")
        lmp.command("run              0")
        lmp.command("undump           2")
        
        #we have to check if the structure melted, otherwise throw and error
        sys = pc.System()
        sys.read_inputfile("traj.melt")
        sys.find_neighbors(method="cutoff", cutoff=0)
        solids = sys.find_solids()
        if (solids/lmp.natoms > 0.5):
            lmp.close()
            raise RuntimeError("System did not melt!")

        #now assign correct temperature
        lmp.command("velocity         all create", self.t, self.t, np.random.randint(0, 10000))
        lmp.command("fix              1 all npt temp", self.t, self.t, self.options["md"]["tdamp"], 
                                      "iso", self.p, self.p, self.options["md"]["pdamp"])
        lmp.command("run              ", int(self.options["md"]["nsmall"])) 
        lmp.command("fix              2 all print 10 \"$(step) $(press) $(vol) $(temp)\" file avg.dat")
        lmp.command("dump             2 all custom", int(self.options["md"]["nsmall"])/10, "traj.dat id type mass x y z vx vy vz")
        lmp.command("run              ", int(self.options["md"]["nlarge"]))

        #finish run
        lmp.close()

    def gather_average_data(self):
        """
        Gather average data
        """
        avgfile = os.path.join(self.simfolder, "avg.dat")
        vol = np.loadtxt(avgfile, usecols=(2,), unpack=True)
        avgvol = np.mean(vol[-100:])
        ncells = self.options["md"]["nx"]*self.options["md"]["ny"]*self.options["md"]["nz"]
        self.natoms = ncells*self.apc
        self.rho = self.natoms/avgvol
        #WARNING: hard coded ufm parameter
        self.eps = self.t*50.0*kb


    def process_traj(self):
        """
        Copy conf
        """
        trajfile = os.path.join(self.simfolder, "traj.dat")
        files = ptp.split_trajectory(trajfile)
        conf = os.path.join(self.simfolder, "conf.dump")

        sys = pc.System()
        sys.read_inputfile(files[-1], customkeys=["vx", "vy", "vz", "mass"])
        sys.to_file(conf, customkeys=["vx", "vy", "vz", "mass"])

        os.remove(trajfile)
        for file in files:
            os.remove(file)


    def write_integrate_script(self, mdscriptfile):
        """
        Write TI integrate script
        """

        self.process_traj()

        with open(mdscriptfile, 'w') as fout:
            lmp.command("label RESTART")

            lmp.command("variable        rnd      equal   round(random(0,999999,%d))"%np.random.randint(0, 10000))


            lmp.command("variable        dt       equal   %f"%self.options["md"]["timestep"])             # Timestep (ps).

            # Adiabatic switching parameters.
            lmp.command("variable        li       equal   1.0")               # Initial lambda.
            lmp.command("variable        lf       equal   0.0")               # Final lambda.
            lmp.command("variable        N_sim    loop    %d"%self.options["main"]["nsims"])                # Number of independent simulations.
            #------------------------------------------------------------------------------------------------------#


            ########################################     Atomic setup     ##########################################
            # Defines the style of atoms, units and boundary conditions.
            lmp.command("units            metal")
            lmp.command("boundary         p p p")
            lmp.command("atom_style       atomic")
            lmp.command("timestep         %f"%self.options["md"]["timestep"])

            # Read atoms positions, velocities and box parameters.
            lmp.command("lattice          %s %f"%(self.l, self.alat))
            lmp.command("region           box block 0 %d 0 %d 0 %d"%(self.options["md"]["nx"], self.options["md"]["ny"], self.options["md"]["nz"]))
            lmp.command("create_box       1 box")

            conf = os.path.join(self.simfolder, "conf.dump")
            lmp.command("read_dump        %s 0 x y z vx vy vz scaled no box yes add keep"%conf)

            lmp.command("neigh_modify    delay 0")

            # Define MEAM and UF potentials parameters.
            lmp.command("pair_style       hybrid/overlay %s ufm 7.5"%self.options["md"]["pair_style"])
            
            #modify pair style
            pc =  self.options["md"]["pair_coeff"]
            pcraw = pc.split()
            #now add style
            pcnew = " ".join([*pcraw[:2], *[self.options["md"]["pair_style"],], *pcraw[2:]])

            lmp.command("pair_coeff       %s"%pcnew)
            lmp.command("pair_coeff       * * ufm %f 1.5"%self.eps) 
            lmp.command("mass             * %f"%self.options["md"]["mass"])

            #------------------------------------------------------------------------------------------------------#


            ################################     Fixes, computes and constraints     ###############################
            # Integrator & thermostat.
            lmp.command("fix             f1 all nve")                              
            lmp.command("fix             f2 all langevin %f %f %f ${rnd}"%(self.t, self.t, self.options["md"]["tdamp"]))
            lmp.command("variable        rnd equal round(random(0,999999,0))")

            # Compute the potential energy of each pair style.
            lmp.command("compute         c1 all pair %s"%self.options["md"]["pair_style"])
            lmp.command("compute         c2 all pair ufm")
            #------------------------------------------------------------------------------------------------------#


            ##########################################     Output setup     ########################################
            # Output variables.
            lmp.command("variable        step equal step")
            lmp.command("variable        dU equal (c_c1-c_c2)/atoms")             # Driving-force obtained from NEHI procedure.

            # Thermo output.
            lmp.command("thermo_style    custom step v_dU")
            lmp.command("thermo          1000")
            #------------------------------------------------------------------------------------------------------#


            ##########################################     Run simulation     ######################################
            # Turn UF potential off (completely) to equilibrate the Sw potential.
            lmp.command("variable        zero equal 0")
            lmp.command("fix             f0 all adapt 0 pair ufm scale * * v_zero")
            lmp.command("run             0")
            lmp.command("unfix           f0")

            # Equilibrate the fluid interacting by Sw potential and switch to UF potential (Forward realization).
            lmp.command("run             %d"%self.options["md"]["te"])

            lmp.command("print           \"${dU} ${li}\" file forward_${N_sim}.dat")
            lmp.command("variable        lambda_sw equal ramp(${li},${lf})")                 # Linear lambda protocol from 1 to 0.
            lmp.command("fix             f3 all adapt 1 pair %s scale * * v_lambda_sw"%self.options["md"]["pair_style"])
            lmp.command("variable        lambda_ufm equal ramp(${lf},${li})")                  # Linear lambda protocol from 0 to 1.
            lmp.command("fix             f4 all adapt 1 pair ufm scale * * v_lambda_ufm")
            lmp.command("fix             f5 all print 1 \"${dU} ${lambda_sw}\" screen no append forward_${N_sim}.dat")
            lmp.command("run             %d"%self.options["md"]["ts"])

            lmp.command("unfix           f3")
            lmp.command("unfix           f4")
            lmp.command("unfix           f5")

            # Equilibrate the fluid interacting by UF potential and switch to sw potential (Backward realization).
            lmp.command("run             %d"%self.options["md"]["te"])

            lmp.command("print           \"${dU} ${lf}\" file backward_${N_sim}.dat")
            lmp.command("variable        lambda_sw equal ramp(${lf},${li})")                 # Linear lambda protocol from 0 to 1.
            lmp.command("fix             f3 all adapt 1 pair %s scale * * v_lambda_sw"%self.options["md"]["pair_style"])
            lmp.command("variable        lambda_ufm equal ramp(${li},${lf})")                  # Linear lambda protocol from 1 to 0.
            lmp.command("fix             f4 all adapt 1 pair ufm scale * * v_lambda_ufm")
            lmp.command("fix             f5 all print 1 \"${dU} ${lambda_sw}\" screen no append backward_${N_sim}.dat")
            lmp.command("run             %d"%self.options["md"]["ts"])

            lmp.command("unfix           f3")
            lmp.command("unfix           f4")
            lmp.command("unfix           f5")
            #------------------------------------------------------------------------------------------------------#


            ##########################################     Loop procedure     ######################################
            lmp.command("next N_sim")
            lmp.command("clear")
            lmp.command("jump %s RESTART"%mdscriptfile)
            #------------------------------------------------------------------------------------------------------#

    
    def thermodynamic_integration(self):
        """
        Perform thermodynamic integration
        """
        w, q, qerr = find_w(self.simfolder, nsims=self.options["main"]["nsims"], 
            full=True)  
        #WARNING: hardcoded UFM parameters           
        f1 = get_uhlenbeck_ford_fe(self.t, 
            self.rho, 50, 1.5)
        f2 = get_ideal_gas_fe(self.t, self.rho, 
            self.natoms, self.options["md"]["mass"], xa=(1-self.c), 
            xb=self.c)
        self.fe = f2 + f1 - w
        self.ferr = qerr

    def submit_report(self):
        report = {}
        report["temperature"] = int(self.t)
        report["pressure"] = float(self.p)
        report["lattice"] = str(self.l)
        report["concentration"] = float(self.c)
        report["rho"] = float(self.rho)
        report["fe"] = float(self.fe)
        report["fe_err"] = float(self.ferr)

        reportfile = os.path.join(self.simfolder, "report.yaml")
        with open(reportfile, 'w') as f:
            yaml.dump(report, f)

    def write_rs_script(self, mdscriptfile):
        """
        Write TI integrate script
        """
        #rev scale needs tstart and tend; here self.t will be start
        #tend will be the final temp
        
        t0 = self.t
        tf = self.options["main"]["temperature"][-1]
        li = 1
        lf = t0/tf

        with open(mdscriptfile, 'w') as fout:
            lmp.command("echo              log")

            lmp.command("variable          T0 equal %f"%t0)  # Initial temperature.
            lmp.command("variable          te equal %d"%self.options["md"]["te"])   # Equilibration time (steps).
            lmp.command("variable          ts equal %d"%self.options["md"]["ts"])  # Switching time (steps).
            lmp.command("variable          li equal %f"%li)
            lmp.command("variable          lf equal %f"%lf)
            lmp.command("variable          rand equal %d"%np.random.randint(0, 1000))


        #-------------------------- Atomic Setup --------------------------------------#  
            lmp.command("units            metal")
            lmp.command("boundary         p p p")
            lmp.command("atom_style       atomic")

            lmp.command("lattice          %s %f"%(self.l, self.alat))
            lmp.command("region           box block 0 %d 0 %d 0 %d"%(self.options["md"]["nx"], self.options["md"]["ny"], self.options["md"]["nz"]))
            lmp.command("create_box       1 box")
            conf = os.path.join(self.simfolder, "conf.dump")
            lmp.command("read_dump        %s 0 x y z vx vy vz scaled no box yes add keep"%conf)


            lmp.command("neigh_modify    every 1 delay 0 check yes once no")

            lmp.command("pair_style       %s"%self.options["md"]["pair_style"])
            lmp.command("pair_coeff       %s"%self.options["md"]["pair_coeff"])
            lmp.command("mass             * %f"%self.options["md"]["mass"])

        #---------------------- Thermostat & Barostat ---------------------------------#
            lmp.command("fix               f1 all nph aniso %f %f %f"%(self.p, self.p, self.options["md"]["pdamp"]))
            lmp.command("fix               f2 all langevin ${T0} ${T0} %f %d zero yes"%(self.options["md"]["tdamp"], np.random.randint(0, 10000)))
            lmp.command("run               ${te}")
            lmp.command("unfix             f1")
            lmp.command("unfix             f2")

            lmp.command("variable         xcm equal xcm(all,x)")
            lmp.command("variable         ycm equal xcm(all,y)")
            lmp.command("variable         zcm equal xcm(all,z)")
            
            lmp.command("fix              f1 all nph aniso %f %f %f fixedpoint ${xcm} ${ycm} ${zcm}"%(self.p, self.p, self.options["md"]["pdamp"]))
            lmp.command("fix              f2 all langevin ${T0} ${T0} %f %d zero yes"%(self.options["md"]["tdamp"], np.random.randint(0, 10000)))
            
        #------------------ Computes, variables & modifications -----------------------#
            lmp.command("compute           tcm all temp/com")
            lmp.command("fix_modify        f1 temp tcm")
            lmp.command("fix_modify        f2 temp tcm")

            lmp.command("variable          step    equal step")
            lmp.command("variable          dU      equal c_thermo_pe/atoms")
            lmp.command("variable          te_run  equal ${te}-1")
            lmp.command("variable          ts_run  equal ${ts}+1")
            lmp.command("thermo_style      custom step pe c_tcm")
            lmp.command("timestep          %f"%self.options["md"]["timestep"])
            lmp.command("thermo            10000")
            

            lmp.command("velocity          all create ${T0} ${rand} mom yes rot yes dist gaussian")   
            lmp.command("variable          i loop %d"%self.options["main"]["nsims"])
            lmp.command("label repetitions")
            lmp.command("    run               ${te}")
            lmp.command("    variable          lambda equal ramp(${li},${lf})")

            #we need to similar to liquid here

            lmp.command("    fix               f3 all adapt 1 pair %s scale * * v_lambda"%self.options["md"]["pair_style"])
            lmp.command("    fix               f4 all print 1 \"${dU} ${lambda}\" screen no file forward_$i.dat")
            lmp.command("    run               ${ts}")
            lmp.command("    unfix             f3")
            lmp.command("    unfix             f4")
            lmp.command("    run               ${te}")
            lmp.command("    variable          lambda equal ramp(${lf},${li})")
            lmp.command("    fix               f3 all adapt 1 pair %s scale * * v_lambda"%self.options["md"]["pair_style"])
            lmp.command("    fix               f4 all print 1 \"${dU} ${lambda}\" screen no file backward_$i.dat")
            lmp.command("    run               ${ts}")
            lmp.command("    unfix             f3")
            lmp.command("    unfix             f4")
            
            lmp.command("next i")
            lmp.command("jump SELF repetitions")