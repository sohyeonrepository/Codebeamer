import unittest
from unittest.mock import Mock
from clients.http import ApiError
from migration.user_batch import source_users, prepare_users, add_users

MAPPING={"phase_1":[
    {"jira":"assignee","cb":{"label":"Assigned to","type":"Members","registration":"assignedTo"},"mode":"mapped"},
    {"jira":"reporter.name","cb":{"label":"Reporter","type":"Choice / User","registration":"customField[6]"},"mode":"mapped"}
]}
DETAIL={"id":5,"type":"UserChoiceField","referenceType":"UserReference",
        "valueModel":"ChoiceFieldValue<UserReference>","multipleValues":True}

class UserTests(unittest.TestCase):
    def test_input_user_dict(self):
        self.assertEqual(source_users({"name":"john"},"assignee"),["john"])

    def test_user_names_from_target(self):
        cb=Mock()
        cb.request.side_effect=[
            DETAIL,
            dict(DETAIL,id=6,multipleValues=False),
            {"id":222,"name":"john"}
        ]
        rows=prepare_users(cb,4879,[{"id":5,"label":"Assigned to"},{"id":6,"label":"Reporter"}],MAPPING)
        data=add_users({"name":"Example"},{"assignee":{"name":"john"},"reporter":None},
                       MAPPING,rows,cb)
        self.assertEqual(data["assignedTo"],{"fieldId":5,"type":"ChoiceFieldValue",
                         "values":[{"id":222,"type":"UserReference"}]})
        cb.request.assert_any_call("GET","/v3/users/findByName?name=john")

    def test_user_mismatch_blocks(self):
        cb=Mock()
        cb.request.return_value={"id":4,"name":"someone_else"}
        with self.assertRaises(ApiError):
            add_users({"name":"N"},{"assignee":{"name":"john"}},
                      {"phase_1":[MAPPING["phase_1"][0]]},
                      {"assignee":(5,DETAIL)},cb)

if __name__=="__main__":
    unittest.main()
