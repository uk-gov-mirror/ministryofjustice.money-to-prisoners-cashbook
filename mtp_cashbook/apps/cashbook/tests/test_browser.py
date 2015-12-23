from django.test import LiveServerTestCase
from selenium import webdriver
from selenium.webdriver.common.keys import Keys

class TestTwo(LiveServerTestCase):

    def __login(self, username, password):
        self.driver.get(self.live_server_url)
        login_field = self.driver.find_element_by_id('id_username')
        login_field.send_keys(username)
        password_field = self.driver.find_element_by_id('id_password')
        password_field.send_keys(password + Keys.RETURN)

    def test_title(self):
        self.driver.get(self.live_server_url)
        heading = self.driver.find_element_by_tag_name('h1')
        self.assertEquals('Money sent to prisoners', heading.text)
        self.assertEquals('32px', heading.value_of_css_property('font-size'))


    def test_bad_login(self):
        self.__login('test-prison-1', 'bad-password')
        self.assertIn('There was a problem submitting the form',
                      self.driver.page_source)

    def test_good_login(self):
        self.__login('test-prison-1', 'test-prison-1')
        self.assertEquals(self.driver.current_url, self.live_server_url + '/')
        self.assertIn('Credits to process', self.driver.page_source)



    def setUp(self):
        self.driver = webdriver.PhantomJS()

    def tearDown(self):
        self.driver.quit()

    def _pre_setup(self, **kwargs):
        """ Override the test database creation """
        pass

    def _post_teardown(self, **kwargs):
        """ Override the test database deletion """
        pass


if __name__ == '__main__':
    unittest.main()
