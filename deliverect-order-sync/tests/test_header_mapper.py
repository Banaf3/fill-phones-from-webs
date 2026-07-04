"""Tests for CSV header mapping."""

from deliverect_sync.importers.header_mapper import HeaderMapper
from deliverect_sync.models import MappingStatus

def test_map_exact():
    mapper = HeaderMapper()
    mappings = mapper.map_headers(["Order ID", "Location", "Order Total"])
    
    assert mapper.mapped_count == 3
    assert mappings[0].canonical_field == "order_id"
    assert mappings[1].canonical_field == "location"
    assert mappings[2].canonical_field == "order_total"

def test_map_aliases():
    mapper = HeaderMapper()
    mappings = mapper.map_headers(["Channel Order ID", "Branch", "Amount"])
    
    assert mapper.mapped_count == 3
    assert mappings[0].canonical_field == "order_id"
    assert mappings[1].canonical_field == "location"
    assert mappings[2].canonical_field == "order_total"

def test_map_arabic():
    mapper = HeaderMapper()
    mappings = mapper.map_headers(["رقم الطلب", "الفرع", "الإجمالي"])
    
    assert mapper.mapped_count == 3
    assert mappings[0].canonical_field == "order_id"
    assert mappings[1].canonical_field == "location"
    assert mappings[2].canonical_field == "order_total"

def test_unmapped():
    mapper = HeaderMapper()
    mappings = mapper.map_headers(["Order ID", "Unknown Field", "Another Unknown"])
    
    assert mapper.mapped_count == 1
    assert len(mapper.unmapped_headers) == 2
    assert mappings[1].status == MappingStatus.UNMAPPED
