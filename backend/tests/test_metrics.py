import pytest
from backend.metrics import agreement, report


def label(identifier, output_id, source, useful, correct, **extra):
    return {"id":identifier,"output_id":output_id,"output_version":1,"source":source,"role":"primary" if source=="human" else "automatic","reviewer":"r" if source=="human" else "model", "created_at":identifier,
        "judgment":{"usefulness":{"value":useful},"correctness":{"value":correct}},**extra}


def outputs():
    return [{"id":f"o{i}","version":1,"status":status,"attempt_started_at":"fixture","tags":["normal"],"method":"fixed","model":None,"generation_seconds":.1} for i,status in enumerate(["completed","completed","failed","timeout"])]


def test_known_confusion_matrix_and_fixed_class_macro_f1():
    result=agreement([("Yes","Yes"),("Yes","No"),("No","No"),("No","No")])
    assert result["confusion_matrix"]=={"Yes":{"Yes":1,"No":1},"No":{"Yes":0,"No":2}}
    assert result["classes"]["Yes"]["precision"]==1
    assert result["classes"]["Yes"]["recall"]==.5
    assert result["classes"]["No"]["f1"]==pytest.approx(.8)
    assert result["macro_f1"]==pytest.approx((2/3+.8)/2)


def test_absent_class_stays_in_macro_and_zero_division_is_zero():
    result=agreement([("Yes","Yes")]*3)
    assert result["macro_f1"]==.5 and result["classes"]["No"]["support"]==0
    assert result["classes"]["No"]["precision"]==0 and result["caution"]
    assert agreement([])["macro_f1"] is None


def test_missing_labels_failures_and_end_to_end_denominator():
    os=outputs()+[{"id":"pending","version":1,"status":"pending","tags":[],"method":"fixed","model":None}]
    labels=[label("1","o0","human","Yes","Yes"),label("2","o1","human",None,"No")]
    result=report(os,labels,{"active_seconds":2})
    q=result["quality"]["human"]["overall"]
    assert q["criteria"]["usefulness"]["yes_rate_assessed"]==1
    assert q["criteria"]["usefulness"]["review_coverage"]==.25
    assert q["both_rate_assessed"]==1 and q["end_to_end_acceptance"]==.25
    assert result["attempted"]==4 and result["not_attempted"]==1
    assert result["evaluator_accuracy"]["usefulness"]["status"]=="Not available: human reference labels required"


def test_pairing_requires_exact_output_and_version():
    os=outputs()
    labels=[label("1","o0","human","Yes","No"),label("2","o1","ai","Yes","No"),label("3","o0","ai","Yes","No",output_version=2)]
    result=report(os,labels,{})
    assert result["evaluator_accuracy"]["usefulness"]["paired_count"]==0
    labels.append(label("4","o0","ai","Yes","No"))
    assert report(os,labels,{})["evaluator_accuracy"]["usefulness"]["paired_count"]==1


def test_ai_and_human_separate_second_review_and_adjudication():
    labels=[label("1","o0","human","Yes","Yes"),label("2","o0","ai","No","No"),label("3","o0","human","No","Yes",reviewer="r2",role="secondary")]
    result=report(outputs(),labels,{})
    assert len(result["disagreements"])==1
    assert result["quality"]["human"]["overall"]["both_yes"]==1
    assert result["quality"]["ai"]["overall"]["both_yes"]==0
    labels.append(label("4","o0","human","No","Yes",reviewer="adjudicator",role="adjudication"))
    after=report(outputs(),labels,{})
    assert after["disagreements"][0]["adjudicated"]
    assert after["quality"]["human"]["overall"]["both_yes"]==0
