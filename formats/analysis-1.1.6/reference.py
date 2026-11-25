"""Optional numerical reference kernels, executed only inside a run sandbox.

Task-specific code, selected columns/windows, conditions and formula mapping
must still be generated and saved. These kernels are not a pre-written report.
"""
import numpy as np


def linear_fit(x,y):
    x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float)
    if len(x)!=len(y) or len(x)<3 or not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError("Invalid paired fit observations")
    centered=x-x.mean();den=np.dot(centered,centered)
    if den<=0:raise ValueError("No distinct independent variable")
    slope=float(np.dot(centered,y-y.mean())/den);intercept=float(y.mean()-slope*x.mean())
    residual=y-(slope*x+intercept)
    return {"slope":slope,"intercept":intercept,"residual":residual,"n":len(x),"slope_standard_error":float(np.sqrt(np.dot(residual,residual)/(len(x)-2)/den))}


def local_cooling_rate(t,temperature,target):
    if not min(temperature)<=target<=max(temperature):raise ValueError("Target temperature outside measured local fit range")
    fit=linear_fit(t,temperature)
    if fit["slope"]>=0:raise ValueError("Selected cooling data do not cool")
    return {**fit,"positive_rate":-fit["slope"],"target_temperature":target}


def steady_conductivity(mass_kg,heat_capacity,thickness_m,sample_diameter_m,disk_thickness_m,disk_diameter_m,delta_k,positive_rate):
    if min(mass_kg,heat_capacity,thickness_m,sample_diameter_m,disk_thickness_m,disk_diameter_m,delta_k,positive_rate)<=0:raise ValueError("Geometry, temperature drop and cooling rate must be positive")
    if sample_diameter_m>disk_diameter_m:raise ValueError("Full-sample-area model requires the sample cross-section to fit within the contact disk; use an explicit contact/spreading model for a larger sample")
    radius=disk_diameter_m/2
    area=np.pi*sample_diameter_m**2/4
    surface_factor=(radius+2*disk_thickness_m)/(2*(radius+disk_thickness_m))
    return {"surface_factor":surface_factor,"sample_area_m2":area,"lambda_W_per_mK":mass_kg*heat_capacity*positive_rate*thickness_m*surface_factor/(area*delta_k)}


def quasisteady(voltage_v,resistance_ohm,area_m2,half_thickness_m,density,delta_k,center_slope):
    if min(resistance_ohm,area_m2,half_thickness_m,density,delta_k,center_slope)<=0:raise ValueError("Missing or invalid quasi-steady physical input")
    flux=voltage_v**2/(2*area_m2*resistance_ohm)
    conductivity=flux*half_thickness_m/(2*delta_k)
    capacity=flux/(density*half_thickness_m*center_slope)
    return {"Jc_W_per_m2":flux,"lambda_W_per_mK":conductivity,"c_J_per_kgK":capacity}


def sampled_electrical_work(t,shunt_voltage,device_voltage,shunt_ohm,sample_interval=None):
    t=np.asarray(t,dtype=float);a=np.asarray(shunt_voltage,dtype=float);b=np.asarray(device_voltage,dtype=float)
    if len(t)<2 or len(t)!=len(a) or len(t)!=len(b) or not np.isfinite(shunt_ohm) or shunt_ohm<=0 or not all(np.isfinite(x).all() for x in (t,a,b)) or not np.all(np.diff(t)>0):raise ValueError("Invalid synchronized voltage/time channels")
    current=a/shunt_ohm;power=current*b
    trapezoid_cumulative=np.cumsum((power[1:]+power[:-1])*np.diff(t)/2)
    elapsed=t[1:]-t[0];trapezoid=float(trapezoid_cumulative[-1])
    out={"current_A":current,"power_signed_W":power,"work_signed_trapezoid_J":trapezoid,"span_seconds":float(elapsed[-1]),
         "trapezoid_cumulative_signed_J":trapezoid_cumulative,"trapezoid_elapsed_seconds":elapsed,
         "trapezoid_running_mean_signed_W":trapezoid_cumulative/elapsed,
         "trapezoid_mean_signed_W":trapezoid/float(elapsed[-1])}
    if sample_interval is not None:
        if not np.isfinite(sample_interval) or sample_interval<=0 or not np.allclose(np.diff(t),sample_interval,atol=1e-8,rtol=1e-8):raise ValueError("Sample interval does not match timestamps")
        cumulative=np.cumsum(power*sample_interval);elapsed_rectangle=(np.arange(len(t))+1)*sample_interval
        out.update(work_signed_rectangle_J=float(cumulative[-1]),rectangle_duration_seconds=float(elapsed_rectangle[-1]),
                   rectangle_cumulative_signed_J=cumulative,rectangle_elapsed_seconds=elapsed_rectangle,
                   rectangle_running_mean_signed_W=cumulative/elapsed_rectangle,
                   rectangle_mean_signed_W=float(cumulative[-1]/elapsed_rectangle[-1]))
    return out
