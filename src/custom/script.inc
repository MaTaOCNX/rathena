// Copyright (c) rAthena Dev Teams - Licensed under GNU GPL
// For more information, see LICENCE in the main folder

/**
 * Custom Script Commands
 * Place the body of custom script commands in this file.
 * Format:
 *	BUILDIN_FUNC(command_name)
 *	{
 *		<code>;
 *	}
 **/

//BUILDIN_FUNC(example)
//{
//	ShowInfo("buildin_example: It works!\n");
//	script_pushint(st,1);
//	return 0;
//}

/**
 * getquestlist {<char_id>};
 * Sets @questlist_id[], @questlist_name$[], @questlist_state[] and @questlist_count
 * with the quests of the character that are not completed yet.
 **/
BUILDIN_FUNC(getquestlist)
{
	struct map_session_data *sd;
	int j = 0;

	if (!script_charid2sd(2,sd))
		return SCRIPT_CMD_FAILURE;

	for (int i = 0; i < sd->num_quests; i++) {
		if (sd->quest_log[i].state == Q_COMPLETE)
			continue;

		std::shared_ptr<s_quest_db> qi = quest_search(sd->quest_log[i].quest_id);

		pc_setreg(sd, reference_uid(add_str("@questlist_id"), j), sd->quest_log[i].quest_id);
		pc_setregstr(sd, reference_uid(add_str("@questlist_name$"), j), qi ? qi->name.c_str() : "Unknown quest");
		pc_setreg(sd, reference_uid(add_str("@questlist_state"), j), sd->quest_log[i].state);
		j++;
	}

	pc_setreg(sd, add_str("@questlist_count"), j);

	return SCRIPT_CMD_SUCCESS;
}
