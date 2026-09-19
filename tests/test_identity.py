from custom_components.atodosol_solarman.common import describe_inverter
from custom_components.atodosol_solarman.const import DOMAIN


def device():
    return {'model':'SG0*LP1','identifiers':{(DOMAIN,'entry'),(DOMAIN,'0')},'serial_number':'0'}


def readings():
    return {'device_serial_number_sensor':('1234567890',None),'device_sensor':('Single-Phase Hybrid Inverter',3),'device_rated_power_sensor':(10000,None),'device_mppts_sensor':(3,None),'device_control_board_firmware_version_sensor':('11028-0000-1727',None)}


def test_real_serial_and_descriptive_model_instead_of_wildcard():
    result=describe_inverter(device(),readings())
    assert result['serial_number']=='1234567890'
    assert result['model']=='SG10LP1'
    assert 'model_id' not in result
    assert (DOMAIN,'0') not in result['identifiers']
    assert result['sw_version']=='11028-0000-1727'


def test_exact_model_is_preserved_without_masking():
    result=describe_inverter(device(),readings(),'SUN-10K-SG02LP1-EU-AM3')
    assert result['model']==result['model_id']=='SUN-10K-SG02LP1-EU-AM3'


def test_reported_model_wins_over_generic_profile():
    result=describe_inverter(device(),readings() | {'device_model_sensor':('SUN-EXACT-123',None)})
    assert result['model']=='SUN-EXACT-123'


def test_detected_power_is_used_in_sg_display():
    result=describe_inverter(device(),readings() | {'device_rated_power_sensor':(5500,None)})
    assert result['model']=='SG5.5LP1'
    assert 'model_id' not in result
