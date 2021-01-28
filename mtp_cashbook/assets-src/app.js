'use strict';

// design systems
import {initAll} from 'govuk-frontend';
initAll();

// mtp common components
import {initDefaults} from 'mtp_common';
import {initStaffDefaults} from 'mtp_common/staff-app';
import {Card} from 'mtp_common/components/card';
import {DialogueBox} from 'mtp_common/components/dialogue-box';
import {MailcheckWarning} from 'mtp_common/components/mailcheck-warning';
import {PrintTrigger} from 'mtp_common/components/print-trigger';
initDefaults();
initStaffDefaults();
Card.init();
DialogueBox.init();
MailcheckWarning.init(
  '.mtp-account-management input[type=email]',
  ['justice.gov.uk'],
  ['gov.uk'],
  []
);
MailcheckWarning.init(
  '#change-your-email #id_email',
  ['justice.gov.uk'],
  ['gov.uk'],
  []
);
MailcheckWarning.init(
  '.mtp-create-disbursement input[type=email]'
);
PrintTrigger.init();
