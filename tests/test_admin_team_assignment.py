"""Admin assignment through real HTTP routes; all accounts are local fixtures."""
import sqlite3
from unittest.mock import patch
from test_v382_finding_submissions import (FindingSubmissionTests, EMPLOYEE_A, EMPLOYEE_B,
                                          MANAGER_A, MANAGER_B, ADMIN, REG_A, workflow)
from scripts import account_role_management_v1 as roles


class AdminTeamTests(FindingSubmissionTests):
    def setUp(self):
        super().setUp()
        self.app.include_router(workflow.router)
        self.app.include_router(roles.router)
        self.stack.enter_context(patch.object(roles, 'connect', self.connect))

    def assign(self, manager, employee=EMPLOYEE_A, actor=ADMIN, headers=None):
        return self.client.put('/manager/admin/team/'+str(employee),
                               headers=headers if headers is not None else self.headers(actor),
                               json={'manager_id': str(manager) if manager else None})

    def test_admin_list_returns_existing_assignment(self):
        response=self.client.get('/admin/roles/users',headers=self.headers(ADMIN))
        self.assertEqual(response.status_code,200,response.text)
        user=next(x for x in response.json() if x['id']==str(EMPLOYEE_A))
        self.assertEqual(user['manager_id'],str(MANAGER_A))
        self.assertEqual(user['manager_email'],'manager-a@example.test')
        for actor in (EMPLOYEE_A,MANAGER_A):
            self.assertEqual(self.client.get('/admin/roles/users',headers=self.headers(actor)).status_code,403)

    def test_assignment_is_admin_only_and_origin_protected(self):
        before=self.rows('account_manager_teams')
        for actor in (EMPLOYEE_A,MANAGER_A):
            self.assertEqual(self.assign(MANAGER_B,actor=actor).status_code,403)
        self.assertEqual(self.assign(MANAGER_B,headers={}).status_code,401)
        h=self.headers(ADMIN);h['origin']='https://evil.example.test'
        self.assertEqual(self.assign(MANAGER_B,headers=h).status_code,403)
        self.assertEqual(self.rows('account_manager_teams'),before)

    def test_invalid_roles_self_and_inactive_manager_rejected(self):
        before=self.rows('account_manager_teams')
        for manager in (ADMIN,EMPLOYEE_B,EMPLOYEE_A):
            self.assertIn(self.assign(manager).status_code,(404,422))
        self.assertEqual(self.assign(MANAGER_A,employee=MANAGER_B).status_code,409)
        with sqlite3.connect(self.identity_path) as db:
            db.execute("UPDATE account_users SET status='blocked' WHERE id=?",(str(MANAGER_B),))
        self.assertEqual(self.assign(MANAGER_B).status_code,404)
        self.assertEqual(self.rows('account_manager_teams'),before)

    def test_reassignment_moves_pending_queue_and_revokes_old_manager(self):
        key=self.key();self.assertEqual(self.submit(key=key).status_code,200)
        self.assertEqual(len(self.queue(MANAGER_A)),1)
        self.assertEqual(self.assign(MANAGER_B).status_code,200)
        self.assertEqual(self.queue(MANAGER_A),[])
        self.assertEqual(len(self.queue(MANAGER_B)),1)
        response=self.client.post(f'/manager/findings/{EMPLOYEE_A}/{REG_A}/{key}/decision',
            headers=self.headers(MANAGER_A),json={'decision':'approved','submission_version':1})
        self.assertEqual(response.status_code,404)
        self.assertEqual(self.decision(manager=MANAGER_B,key=key).status_code,200)
        self.assertEqual(self.queue(MANAGER_B)[0]['status'],'approved')

    def test_reassignment_preserves_terminal_decision_and_actor(self):
        self.submit();self.assertEqual(self.decision().status_code,200)
        reviews=self.rows('account_manager_finding_reviews')
        self.assertEqual(self.assign(MANAGER_B).status_code,200)
        self.assertEqual(self.rows('account_manager_finding_reviews'),reviews)
        self.assertEqual(self.queue(MANAGER_A),[])
        self.assertEqual(self.queue(MANAGER_B)[0]['status'],'approved')
        self.assertTrue(self.submit().json()['already_submitted'])

    def test_unassign_blocks_submission_and_reassign_restores_routing(self):
        self.assertEqual(self.assign(None).status_code,200)
        self.assertFalse(self.get_findings().json()['manager_assigned'])
        self.assertEqual(self.submit().status_code,409)
        self.assertEqual(self.assign(MANAGER_B).status_code,200)
        self.assertEqual(self.submit().status_code,200)
        self.assertEqual(self.queue(MANAGER_A),[])
        self.assertEqual(len(self.queue(MANAGER_B)),1)

    def test_inactive_employee_can_only_be_detached(self):
        with sqlite3.connect(self.identity_path) as db:
            db.execute("UPDATE account_users SET status='blocked' WHERE id=?",(str(EMPLOYEE_A),))
        self.assertEqual(self.assign(MANAGER_B).status_code,409)
        self.assertEqual(self.assign(None).status_code,200)

    def test_inactive_manager_cannot_receive_new_submissions(self):
        with sqlite3.connect(self.identity_path) as db:
            db.execute("UPDATE account_users SET status='blocked' WHERE id=?",(str(MANAGER_A),))
        self.assertFalse(self.get_findings().json()['manager_assigned'])
        self.assertEqual(self.submit().status_code,409)

    def attach(self,employee=EMPLOYEE_A,actor=MANAGER_B,body=None):
        return self.client.post('/manager/team/'+str(employee)+'/attach',
                                headers=self.headers(actor),json=body)

    def test_manager_only_sees_unassigned_approved_staff(self):
        self.assign(None)
        response=self.client.get('/manager/team/available',headers=self.headers(MANAGER_B))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([r['id'] for r in response.json()],[str(EMPLOYEE_A)])
        self.assertEqual(set(response.json()[0]),{'id','email','full_name'})
        for actor in (EMPLOYEE_A,ADMIN):
            self.assertEqual(self.client.get('/manager/team/available',headers=self.headers(actor)).status_code,403)

    def test_manager_self_attach_ignores_other_manager_in_body(self):
        self.assign(None)
        response=self.attach(body={'manager_id':str(MANAGER_A),'role':'admin'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['manager'],str(MANAGER_B))
        self.assertFalse(self.attach().json()['changed'])
        self.assertEqual(self.submit().status_code,200)
        self.assertEqual(len(self.queue(MANAGER_B)),1)
        self.assertEqual(self.queue(MANAGER_A),[])
        with sqlite3.connect(self.identity_path) as db:
            self.assertEqual(db.execute('SELECT role FROM account_users WHERE id=?',(str(EMPLOYEE_A),)).fetchone()[0],'user')

    def test_manager_cannot_poach_or_attach_invalid_role(self):
        self.assertEqual(self.attach().status_code,409)
        for target in (MANAGER_A,ADMIN):
            self.assertEqual(self.attach(employee=target).status_code,409)
        self.assertEqual(self.attach(employee=MANAGER_B).status_code,422)
        for actor in (EMPLOYEE_A,ADMIN):
            self.assertEqual(self.attach(actor=actor).status_code,403)

    def test_self_attach_checks_origin_and_inactive_employee(self):
        self.assign(None)
        h=self.headers(MANAGER_B);h['origin']='https://evil.example.test'
        self.assertEqual(self.client.post('/manager/team/'+str(EMPLOYEE_A)+'/attach',headers=h).status_code,403)
        with sqlite3.connect(self.identity_path) as db:
            db.execute("UPDATE account_users SET status='blocked' WHERE id=?",(str(EMPLOYEE_A),))
        self.assertEqual(self.attach().status_code,409)
        self.assertEqual(self.client.get('/manager/team/available',headers=self.headers(MANAGER_B)).json(),[])

    def test_self_attach_restores_preexisting_pending_queue(self):
        self.submit();self.assign(None)
        self.assertEqual(self.queue(MANAGER_A),[])
        self.assertEqual(self.attach().status_code,200)
        self.assertEqual(len(self.queue(MANAGER_B)),1)
        self.assertEqual(self.decision(manager=MANAGER_B).status_code,200)
