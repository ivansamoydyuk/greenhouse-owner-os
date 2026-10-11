from decimal import Decimal
from uuid import UUID
import pytest
from pydantic import ValidationError
from greenhouse_owner_os.domain.harvest_journey import apportion,JourneyEstimate
A=UUID(int=1);B=UUID(int=2);C=UUID(int=3)
def test_per_line_largest_remainder_exact_conservation_and_stable_tie():
    for quantum in ('.001','.01'):
        x=apportion(quantum,quantum,{C:1,B:1,A:1},3)
        assert x=={str(A):Decimal(quantum),str(B):0,str(C):0}
        assert x==apportion(quantum,quantum,{A:1,B:1,C:1},3)
        assert sum(x.values())==Decimal(quantum)
def test_proportions_never_create_quality_count_and_partial_basis_retained():
    assert apportion(900,'.001',{A:100,B:50},150)=={str(A):Decimal('600'),str(B):Decimal('300')}
    assert apportion(45000,'.01',{A:100,B:50},150)=={str(A):Decimal('30000'),str(B):Decimal('15000')}
    assert apportion(900,'.001',{A:100},150)=={str(A):Decimal('600'),None:Decimal('300')}
@pytest.mark.parametrize('value',[0,-1,1.5])
def test_invalid_basis_never_fabricates_distribution(value):
    with pytest.raises(ValidationError):JourneyEstimate(expected_version=0,confirmed=True,reason='review',basis=[dict(work_area_id=A,basis_count=value)],total_basis_count='10',basis_description='explicit')
def test_duplicate_scope_and_overfull_basis_rejected():
    for basis in ([dict(work_area_id=A,basis_count='2'),dict(work_area_id=A,basis_count='2')],[dict(work_area_id=A,basis_count='11')]):
        with pytest.raises(ValidationError):JourneyEstimate(expected_version=0,confirmed=True,reason='review',basis=basis,total_basis_count='10',basis_description='explicit')
def test_maximum_precision_integer_remainders_do_not_round_authority():
    amount=Decimal('999999999999999999.99')
    basis={A:Decimal('499999999999999.999'),B:Decimal('500000000000000.000')}
    total=sum(basis.values())
    x=apportion(amount,'.01',basis,total)
    assert sum(x.values())==amount
    assert x==apportion(amount,'.01',dict(reversed(list(basis.items()))),total)
    units=int(amount*100);den=int(total*1000)
    expected={str(k):divmod(units*int(v*1000),den) for k,v in basis.items()}
    for k,val in x.items():assert int(val*100) in (expected[k][0],expected[k][0]+1)
