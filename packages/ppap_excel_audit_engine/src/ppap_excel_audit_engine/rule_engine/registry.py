from __future__ import annotations

from collections.abc import Callable

from ppap_core.types import PpapDocumentType

from .contracts import RuleResource
from .resources.doc01_dimension_measurement import get_doc01_resource_view
from .resources.doc02_1_dvp_r import get_doc02_1_resource_view
from .resources.doc02_2_dpvr import get_doc02_2_resource_view
from .resources.doc03_sub_supplier import get_doc03_resource_view
from .resources.doc04_checking_fixture_approval import get_doc04_resource_view
from .resources.doc05_tooling_approval import get_doc05_resource_view
from .resources.doc06_msa_report import get_doc06_resource_view
from .resources.doc07_initial_process_capacity import get_doc07_resource_view
from .resources.doc08_process_flow_chart import get_doc08_resource_view
from .resources.doc09_pfmea import get_doc09_resource_view
from .resources.doc10_control_plan import get_doc10_resource_view
from .resources.doc11_process_special_characteristic import get_doc11_resource_view
from .resources.doc12_equipment_list import get_doc12_resource_view
from .resources.doc13_plant_capacity_evaluation import get_doc13_resource_view
from .resources.doc14_xir_assembly_issue import get_doc14_resource_view
from .resources.doc15_annual_test_requirements import get_doc15_resource_view
from .resources.doc16_psw import get_doc16_resource_view

_DOC_TYPE_RESOURCE_REGISTRY: dict[PpapDocumentType, Callable[[], RuleResource]] = {
    PpapDocumentType.DIMENSION_MEASUREMENT_REPORT: get_doc01_resource_view,
    PpapDocumentType.DVP_R: get_doc02_1_resource_view,
    PpapDocumentType.DPVR: get_doc02_2_resource_view,
    PpapDocumentType.SUB_SUPPLIER_LIST: get_doc03_resource_view,
    PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT: get_doc04_resource_view,
    PpapDocumentType.TOOLING_APPROVAL_REPORT: get_doc05_resource_view,
    PpapDocumentType.MSA_REPORT: get_doc06_resource_view,
    PpapDocumentType.INITIAL_PROCESS_CAPACITY_STUDY: get_doc07_resource_view,
    PpapDocumentType.PROCESS_FLOW_CHART: get_doc08_resource_view,
    PpapDocumentType.PFMEA: get_doc09_resource_view,
    PpapDocumentType.CONTROL_PLAN: get_doc10_resource_view,
    PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST: get_doc11_resource_view,
    PpapDocumentType.EQUIPMENT_LIST: get_doc12_resource_view,
    PpapDocumentType.PLANT_CAPACITY_EVALUATION_REPORT: get_doc13_resource_view,
    PpapDocumentType.XIR_ASSEMBLY_ISSUE_REPORT: get_doc14_resource_view,
    PpapDocumentType.ANNUAL_TEST_REQUIREMENTS: get_doc15_resource_view,
    PpapDocumentType.PSW: get_doc16_resource_view,
}


def get_doc_resource_by_doc_type(doc_type: PpapDocumentType) -> RuleResource | None:
    getter = _DOC_TYPE_RESOURCE_REGISTRY.get(doc_type)
    if getter is None:
        return None
    return getter()
