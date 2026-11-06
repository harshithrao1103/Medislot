import sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'lambdas/_shared'),str(Path(__file__).resolve().parents[1]/'lambdas/patient_domain')]
from patient_domain_lambda import patient_identity,BadRequest,positive,lambda_handler

def test_identity_from_authorizer_only():
    event={'requestContext':{'authorizer':{'role':'patient','userId':'12'}},'pathParameters':{'id':'999'}}
    assert patient_identity(event)==12
    with pytest.raises(BadRequest):patient_identity({'requestContext':{'authorizer':{'role':'doctor','userId':'12'}}})

def test_positive_ids():
    assert positive('3','doctorId')==3
    with pytest.raises(BadRequest):positive('-2','doctorId')

def test_patient_route_requires_authorizer():
    response=lambda_handler({'resource':'/patients/me','httpMethod':'GET','requestContext':{'authorizer':{'role':'doctor','userId':'3'}}},None)
    assert response['statusCode']==403

def test_public_search_pagination_validation():
    response=lambda_handler({'resource':'/doctors','httpMethod':'GET','queryStringParameters':{'page_size':'999'}},None)
    assert response['statusCode']==400
